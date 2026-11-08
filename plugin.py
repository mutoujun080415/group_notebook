"""群聊摘录（Group Notebook）插件入口。

把群里一天的聊天记录整理成一页手账长图：统计邮票卡、24 小时活跃轨迹、
今日话题、群友侧写、页角金句与聊天天气，可定时投递，也可以 ``/摘录``
手动补发。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BasePlugin, register_plugin
from src.app.plugin_system.types import ChatStream
from src.kernel.concurrency import get_task_manager
from src.kernel.scheduler import TriggerType, get_unified_scheduler

from .command import NotebookCommand, NotebookWeeklyCommand, _parse_permission
from .config import GroupNotebookConfig
from .renderer import shutdown_browser
from .router import NotebookWebUIRouter
from .service import NotebookService

logger = get_logger("group_notebook")


@register_plugin
class GroupNotebookPlugin(BasePlugin):
    """群聊摘录插件。"""

    plugin_name = "group_notebook"
    configs: list[type] = [GroupNotebookConfig]
    dependencies: list[str] = []

    def __init__(self, config: GroupNotebookConfig | None = None) -> None:
        """初始化插件状态。"""
        super().__init__(config)
        self.notebook: NotebookService | None = None
        self._schedule_id: str | None = None
        self._register_task_id: str | None = None
        self._state_path = Path("data") / "group_notebook" / "state.json"
        self._last_sent_day = ""
        self._last_sent_week = ""
        self._retry_after: datetime | None = None

    def _config(self) -> GroupNotebookConfig:
        """读取配置，未注入时使用默认值。"""
        return self.config if isinstance(self.config, GroupNotebookConfig) else GroupNotebookConfig()

    def get_components(self) -> list[type]:
        """返回插件组件；独立页面由 ``webui.mount_on_main_http`` 决定是否挂载。"""
        components: list[type] = [NotebookService, NotebookCommand, NotebookWeeklyCommand]
        if self._config().webui.mount_on_main_http:
            components.append(NotebookWebUIRouter)
        return components

    # ------------------------------------------------------------ 生命周期

    async def on_plugin_loaded(self) -> None:
        """初始化服务、权限与定时任务。"""
        config = self._config()
        self.notebook = NotebookService(self)
        NotebookCommand.permission_level = _parse_permission(config.general.command_permission)
        NotebookCommand.associated_platforms = [config.general.platform]
        NotebookWeeklyCommand.permission_level = _parse_permission(config.general.command_permission)
        NotebookWeeklyCommand.associated_platforms = [config.general.platform]
        self._load_state()

        if not config.general.enabled:
            logger.info("群聊摘录已在配置中关闭")
            return
        if not config.general.auto_send:
            logger.info("群聊摘录已关闭自动发送，仅保留 /摘录 手动触发")
            return
        if not config.general.groups:
            logger.info("群聊摘录未配置群号，跳过定时任务注册")
            return

        task = get_task_manager().create_task(
            self._register_schedule_when_ready(),
            name="group_notebook_register",
            daemon=True,
        )
        self._register_task_id = task.task_id
        logger.info("群聊摘录插件已加载，等待调度器就绪")

    async def on_plugin_unloaded(self) -> None:
        """移除定时任务、关闭浏览器并清理句柄。"""
        if self._schedule_id is not None:
            try:
                await get_unified_scheduler().remove_schedule(self._schedule_id)
            except Exception as exc:
                logger.warning(f"移除群聊摘录定时任务失败：{exc}")
            self._schedule_id = None
        if self._register_task_id is not None:
            get_task_manager().cancel_task(self._register_task_id)
            self._register_task_id = None
        try:
            await shutdown_browser()
        except Exception as exc:
            logger.warning(f"关闭截图浏览器失败：{exc}")
        self.notebook = None

    async def _register_schedule_when_ready(self) -> None:
        """等待调度器启动后注册周期检查。"""
        config = self._config()
        interval = max(30, config.general.check_interval_seconds)
        scheduler = get_unified_scheduler()
        for _ in range(600):
            try:
                self._schedule_id = await scheduler.create_schedule(
                    callback=self._tick,
                    trigger_type=TriggerType.TIME,
                    trigger_config={"interval_seconds": interval},
                    is_recurring=True,
                    task_name="group_notebook_tick",
                    force_overwrite=True,
                )
                self._register_task_id = None
                logger.info(f"群聊摘录定时检查已注册：{self._schedule_id}（间隔 {interval}s）")
                return
            except RuntimeError:
                await asyncio.sleep(0.5)
            except Exception as exc:
                logger.error(f"注册群聊摘录定时任务失败：{exc}", exc_info=True)
                self._register_task_id = None
                return
        self._register_task_id = None
        logger.error("等待调度器就绪超时，群聊摘录定时任务未注册")

    # ------------------------------------------------------------ 定时执行

    async def _tick(self) -> None:
        """每分钟检查一次：日报与周报的定时投递。"""
        config = self._config()
        if not config.general.enabled:
            return
        if self.notebook is None:
            self.notebook = NotebookService(self)

        now = datetime.now()
        if config.general.auto_send and config.general.groups:
            await self._tick_daily(now)
        await self._tick_weekly(now)

    async def _tick_daily(self, now: datetime) -> None:
        """日报定时投递。"""
        config = self._config()
        if not config.general.auto_send or not config.general.groups:
            return
        if self.notebook is None:
            self.notebook = NotebookService(self)
        if self._retry_after is not None and now < self._retry_after:
            return
        if not self.notebook.is_due(now):
            return

        day = self.notebook.report_day(now)
        if day == self._last_sent_day:
            return

        names = self.notebook.group_room_names()
        success_count = 0
        for raw_group in config.general.groups:
            group_id = str(raw_group).strip()
            if not group_id:
                continue
            stream_id = ChatStream.generate_stream_id(config.general.platform, group_id=group_id)
            try:
                ok, message = await self.notebook.send(
                    stream_id,
                    day,
                    room_name=names.get(group_id, ""),
                )
            except Exception as exc:
                logger.error(f"群聊摘录投递异常：group={group_id} day={day} {exc}", exc_info=True)
                continue
            if ok:
                success_count += 1
            else:
                logger.warning(f"群聊摘录投递失败：group={group_id} day={day} {message}")

        if success_count == 0:
            # 全部失败（例如适配器离线、当天没消息）时十分钟后重试，避免整天空转。
            self._retry_after = now + timedelta(minutes=10)
            logger.warning(f"群聊摘录本轮全部失败，{self._retry_after:%H:%M} 后重试")
            return

        self._retry_after = None
        self._last_sent_day = day
        self._save_state()
        logger.info(f"群聊摘录定时投递完成：day={day}，成功 {success_count}/{len(config.general.groups)} 个群")

    async def _tick_weekly(self) -> None:
        """周报定时投递：到达配置的星期/时刻后，给每个群发当周周记。"""
        config = self._config()
        if not config.general.enabled or not config.general.weekly_auto_send:
            return
        if self.notebook is None:
            self.notebook = NotebookService(self)
        now = datetime.now()
        if not self.notebook.is_week_due(now):
            return
        monday = self.notebook.current_week_monday(now)
        if monday == self._last_sent_week:
            return
        names = self.notebook.group_room_names()
        success_count = 0
        for raw_group in config.general.groups:
            group_id = str(raw_group).strip()
            if not group_id:
                continue
            stream_id = ChatStream.generate_stream_id(config.general.platform, group_id=group_id)
            try:
                ok, message = await self.notebook.send_week(
                    stream_id,
                    monday,
                    room_name=names.get(group_id, ""),
                )
            except Exception as exc:
                logger.error(f"群聊周记投递异常：group={group_id} week={monday} {exc}", exc_info=True)
                continue
            if ok:
                success_count += 1
            else:
                logger.warning(f"群聊周记投递失败：group={group_id} week={monday} {message}")

        if success_count == 0:
            logger.warning("群聊周记本轮全部失败，下个 tick 重试")
            return
        self._last_sent_week = monday
        self._save_state()
        logger.info(f"群聊周记定时投递完成：week={monday}，成功 {success_count}/{len(config.general.groups)} 个群")

    # ------------------------------------------------------------ 状态持久化

    def _load_state(self) -> None:
        """读取上次发送日期，避免重启后重复投递。"""
        try:
            if self._state_path.exists():
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                self._last_sent_day = str(data.get("last_sent_day") or "")
                self._last_sent_week = str(data.get("last_sent_week") or "")
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"读取群聊摘录状态失败：{exc}")
            self._last_sent_day = ""

    def _save_state(self) -> None:
        """写入上次发送日期。"""
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps({"last_sent_day": self._last_sent_day,
                        "last_sent_week": self._last_sent_week}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning(f"写入群聊摘录状态失败：{exc}")

    # ------------------------------------------------------------ 可选入口

    async def generate_for_group(self, group_id: str, day: str = "") -> tuple[bool, str, Any]:
        """供其他组件调用的便捷方法：按群号生成摘录。"""
        if self.notebook is None:
            self.notebook = NotebookService(self)
        config = self._config()
        stream_id = ChatStream.generate_stream_id(config.general.platform, group_id=group_id)
        return await self.notebook.generate(
            stream_id,
            day,
            room_name=self.notebook.group_room_names().get(group_id, ""),
        )
