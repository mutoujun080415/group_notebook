"""``/摘录`` 命令：手动生成并发送群聊摘录手账。"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseCommand, cmd_route
from src.app.plugin_system.types import PermissionLevel

from .collector import resolve_timezone, today_in_timezone
from .config import GroupNotebookConfig

logger = get_logger("group_notebook")


def _parse_permission(value: str) -> PermissionLevel:
    """把配置中的权限字符串转换为枚举。"""
    text = str(value or "").strip().lower()
    mapping = {
        "guest": PermissionLevel.GUEST,
        "user": PermissionLevel.USER,
        "operator": PermissionLevel.OPERATOR,
        "owner": PermissionLevel.OWNER,
    }
    return mapping.get(text, PermissionLevel.USER)


class NotebookCommand(BaseCommand):
    """通过 ``/摘录 [日期]`` 手动生成群聊摘录。"""

    name = "摘录"
    description = "生成并发送指定日期的群聊摘录手账长图（默认今天）"
    permission_level = PermissionLevel.USER
    associated_platforms: list[str] = ["qq"]
    command_prefix = "/"

    def __init__(
        self,
        plugin: Any,
        stream_id: str,
        message_id: str = "",
        message: Any | None = None,
    ) -> None:
        """初始化命令，并按配置调整权限级别。"""
        super().__init__(plugin, stream_id, message_id, message)
        config = getattr(plugin, "config", None)
        if isinstance(config, GroupNotebookConfig):
            self.permission_level = _parse_permission(config.general.command_permission)

    @classmethod
    def match(cls, parts: list[str]) -> int:
        """同时接受中文命令与英文别名。"""
        if parts and parts[0].lower() in {"摘录", "notebook", "群聊摘录"}:
            return 1
        return 0

    def _config(self) -> GroupNotebookConfig:
        """读取插件配置。"""
        config = getattr(self.plugin, "config", None)
        return config if isinstance(config, GroupNotebookConfig) else GroupNotebookConfig()

    def _resolve_day(self, text: str) -> tuple[bool, str]:
        """解析用户输入的日期，返回 (是否合法, 日期字符串或错误信息)。"""
        raw = str(text or "").strip()
        config = self._config()
        timezone_name = config.general.timezone
        today = date_cls.fromisoformat(today_in_timezone(timezone_name))
        if not raw:
            return True, today.isoformat()
        if raw in {"今天", "today", "今日"}:
            return True, today.isoformat()
        if raw in {"昨天", "yesterday", "昨日"}:
            return True, (today - timedelta(days=1)).isoformat()

        candidate = raw
        month_day = candidate.split("-")
        if len(month_day) == 2 and all(part.isdigit() for part in month_day):
            candidate = f"{today.year}-{int(month_day[0]):02d}-{int(month_day[1]):02d}"
        try:
            parsed = date_cls.fromisoformat(candidate)
        except ValueError:
            return False, "日期格式不对，用 2026-11-05 或 11-05 这种写法"
        if parsed > today:
            return False, "未来的摘录还没发生，等那天过完再来"
        if parsed < today - timedelta(days=400):
            return False, "太久以前的记录就不翻了，换一天吧"
        return True, parsed.isoformat()

    @cmd_route()
    async def handle_default(self, date_text: str = "") -> tuple[bool, str]:
        """生成并发送摘录。

        Args:
            date_text: 可选日期，支持 ``2026-11-05``、``11-05``、``昨天``。

        Returns:
            ``(是否成功, 说明文本)``。
        """
        ok, day = self._resolve_day(date_text)
        if not ok:
            return False, day

        service = getattr(self.plugin, "notebook", None)
        if service is None:
            return False, "摘录服务没准备好，稍后再试"

        config = self._config()
        logger.info(f"/摘录 触发：stream={self.stream_id} day={day}")
        try:
            sent, message = await service.send(
                self.stream_id,
                day,
                room_name=config.notebook.room_name,
            )
        except Exception as exc:
            logger.error(f"/摘录 执行失败：{exc}", exc_info=True)
            return False, f"摘录生成失败：{exc}"
        return sent, message

    @cmd_route("预览")
    async def handle_preview(self, date_text: str = "") -> tuple[bool, str]:
        """只生成手账、不发送，方便检查排版。"""
        ok, day = self._resolve_day(date_text)
        if not ok:
            return False, day
        service = getattr(self.plugin, "notebook", None)
        if service is None:
            return False, "摘录服务没准备好，稍后再试"
        success, message = await service.preview(self.stream_id, day)
        return success, message

    @cmd_route("重做")
    async def handle_regenerate(self, date_text: str = "") -> tuple[bool, str]:
        """忽略旧图，重新生成并发送摘录。"""
        ok, day = self._resolve_day(date_text)
        if not ok:
            return False, day
        service = getattr(self.plugin, "notebook", None)
        if service is None:
            return False, "摘录服务没准备好，稍后再试"
        config = self._config()
        try:
            sent, message = await service.send(
                self.stream_id,
                day,
                room_name=config.notebook.room_name,
                regenerate=True,
            )
        except Exception as exc:
            logger.error(f"/摘录 重做失败：{exc}", exc_info=True)
            return False, f"摘录重做失败：{exc}"
        return sent, message

    def current_time(self) -> datetime:
        """返回配置时区下的当前时间。"""
        return datetime.now(resolve_timezone(self._config().general.timezone))
