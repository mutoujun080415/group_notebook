"""群聊摘录生成与投递服务。

职责链：读取消息 → 统计 → LLM/规则文案 → 渲染手账长图 → 落盘 → 发送。
Service 不缓存跨调用状态，所有中间结果都写入 ``data/group_notebook``。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import date as date_cls
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseService

from .analyzer import analyze_day
from .collector import (
    build_transcript,
    compute_stats,
    day_bounds,
    fetch_day_messages,
    resolve_timezone,
    sample_messages,
    split_bot_messages,
    today_in_timezone,
)
from .config import GroupNotebookConfig
from .models import NotebookReport
from .renderer import NotebookRenderer
from .reports import ReportEntry, list_reports, slug_for_stream

logger = get_logger("group_notebook")

_VERSION = "0.3.0"


class NotebookService(BaseService):
    """生成并投递群聊摘录手账。"""

    name = "notebook_service"
    description = "统计群聊消息、生成摘录文案并渲染成手账风长图"
    version = _VERSION
    dependencies: list[str] = []

    def __init__(self, plugin: Any | None = None) -> None:
        """初始化服务。"""
        super().__init__(plugin)
        self._locks: dict[str, asyncio.Lock] = {}
        self._renderer: NotebookRenderer | None = None
        self._pillow_renderer: Any | None = None

    # ------------------------------------------------------------ 基础访问

    def _config(self) -> GroupNotebookConfig:
        """读取插件配置，未加载时退回默认配置。"""
        config = getattr(self.plugin, "config", None)
        return config if isinstance(config, GroupNotebookConfig) else GroupNotebookConfig()

    def renderer(self) -> NotebookRenderer:
        """返回（惰性创建的）手账渲染器。"""
        if self._renderer is None:
            self._renderer = NotebookRenderer(
                self._config(), base_dir=Path(__file__).resolve().parent
            )
        return self._renderer

    async def aclose(self) -> None:
        """释放渲染器持有的浏览器资源。"""
        if self._renderer is not None:
            await self._renderer.shutdown()
            self._renderer = None

    def data_dir(self) -> Path:
        """插件数据目录。"""
        return Path("data") / "group_notebook"

    def report_dir(self, stream_id: str) -> Path:
        """指定聊天流的摘录产物目录。"""
        return self.data_dir() / "reports" / slug_for_stream(stream_id)

    def render_dir(self, stream_id: str) -> Path:
        """指定聊天流的渲染中间 HTML 目录。"""
        return self.data_dir() / "render" / slug_for_stream(stream_id)

    def list_reports(self, limit: int = 60) -> list[ReportEntry]:
        """列出已生成的历史摘录。"""
        return list_reports(self.data_dir(), limit=limit)

    def report_paths(self, stream_id: str, day: str) -> tuple[Path, Path, Path]:
        """返回 (PNG 路径, JSON 路径, HTML 路径)。"""
        folder = self.report_dir(stream_id)
        html_folder = self.render_dir(stream_id)
        return (
            folder / f"{day}.png",
            folder / f"{day}.json",
            html_folder / f"{day}.html",
        )

    def _lock(self, key: str) -> asyncio.Lock:
        """获取按 key 隔离的异步锁。"""
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def room_name_for(self, stream_id: str, fallback: str = "") -> str:
        """根据配置的群号别名解析群名。"""
        config = self._config()
        return fallback.strip() or config.notebook.room_name

    # ------------------------------------------------------------ 主流程

    async def generate(
        self,
        stream_id: str,
        day: str = "",
        *,
        room_name: str = "",
        force: bool = False,
    ) -> tuple[bool, str, Path | None]:
        """生成指定聊天流某天的群聊摘录。

        Returns:
            ``(是否成功, 说明文本, PNG 路径)``。
        """
        config = self._config()
        timezone_name = config.general.timezone
        target_day = day.strip() or today_in_timezone(timezone_name)
        try:
            date_cls.fromisoformat(target_day)
        except ValueError:
            return False, f"日期格式不对：{target_day}（应为 YYYY-MM-DD）", None

        png_path, json_path, html_path = self.report_paths(stream_id, target_day)
        if png_path.exists() and not force:
            return True, f"{target_day} 的摘录已存在", png_path

        async with self._lock(f"{stream_id}:{target_day}"):
            if png_path.exists() and not force:
                return True, f"{target_day} 的摘录已存在", png_path
            return await self._build(stream_id, target_day, room_name, png_path, json_path, html_path)

    async def _build(
        self,
        stream_id: str,
        day: str,
        room_name: str,
        png_path: Path,
        json_path: Path,
        html_path: Path,
    ) -> tuple[bool, str, Path | None]:
        """执行一次完整的生成流程。"""
        config = self._config()
        timezone_name = config.general.timezone
        start_ts, end_ts = day_bounds(day, timezone_name)
        logger.info(f"开始生成群聊摘录：stream={stream_id} day={day}")

        messages = await fetch_day_messages(
            stream_id,
            day,
            timezone_name,
            include_bot=True,
            max_messages=0,
        )
        if not messages:
            return False, f"{day} 这一天没有聊天记录，摘录没东西可写", None

        human_messages, bot_messages = await split_bot_messages(messages)
        stats_messages = messages if config.analysis.include_bot else human_messages
        stats = compute_stats(
            stats_messages,
            day=day,
            timezone_name=timezone_name,
            bot_messages=bot_messages,
        )

        transcript_source = sample_messages(messages, config.analysis.max_messages)
        transcript = build_transcript(
            transcript_source,
            timezone_name=timezone_name,
            per_message_limit=config.analysis.message_char_limit,
            budget=config.analysis.transcript_budget,
        )

        report = await analyze_day(
            report_date=day,
            room_name=self.room_name_for(stream_id, room_name),
            bot_name=config.notebook.bot_name,
            page_title=config.resolved_page_title(),
            stats=stats,
            messages=stats_messages,
            transcript=transcript,
            config=config,
        )

        try:
            engine_used = await self._render_report(report, png_path, html_path)
        except Exception as exc:
            logger.error(f"渲染群聊摘录失败：{exc}", exc_info=True)
            return False, f"手账渲染失败：{exc}", None

        try:
            await asyncio.to_thread(
                self._save_report, png_path, json_path, report.to_dict()
            )
        except Exception as exc:
            logger.error(f"保存群聊摘录失败（旧文件已保留）：{exc}", exc_info=True)
            return False, f"摘录保存失败：{exc}", None
        self._cleanup_old_reports()
        logger.info(
            f"群聊摘录已生成：{png_path}（引擎 {engine_used}，来源 {report.source}，"
            f"消息 {stats.message_count} 条）"
        )
        return True, (
            f"{day} 的摘录已生成（{stats.message_count} 条消息，{engine_used} 引擎）"
        ), png_path

    async def _render_report(self, report: NotebookReport, png_path: Path, html_path: Path) -> str:
        """按配置选择渲染引擎；auto 模式在浏览器不可用/渲染失败时自动回退 Pillow。"""
        from .renderer import pick_engine

        engine = pick_engine(self._config().notebook)
        if engine == "playwright":
            try:
                await self.renderer().render(report, png_path, html_path)
                return "playwright"
            except Exception as exc:
                if str(self._config().notebook.render_engine or "auto").strip().lower() == "playwright":
                    raise  # 显式指定 playwright 时直接报错，提示用户换引擎
                logger.warning(f"Playwright 渲染失败（{exc}），自动回退 Pillow 引擎")
        pillow = self.pillow_renderer()
        await asyncio.to_thread(pillow.render, report, png_path)
        return "pillow"

    def pillow_renderer(self):
        """惰性创建 Pillow 渲染器。"""
        if self._pillow_renderer is None:
            from .pillow_renderer import PillowNotebookRenderer

            self._pillow_renderer = PillowNotebookRenderer(
                self._config(), base_dir=Path(__file__).resolve().parent
            )
        return self._pillow_renderer

    @staticmethod
    def _save_report(png_path: Path, json_path: Path, payload: dict[str, Any]) -> None:
        """把 JSON 原子写入磁盘（PNG 由渲染器先写临时名再截图，无需重复处理）。"""
        json_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_json = json_path.with_name(f".{json_path.name}.tmp")
        try:
            tmp_json.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp_json, json_path)
        finally:
            try:
                if tmp_json.exists():
                    tmp_json.unlink()
            except OSError:
                pass

    def _cleanup_old_reports(self) -> None:
        """按配置清理过期的图片、JSON 与渲染 HTML。"""
        keep_days = int(self._config().storage.keep_days)
        if keep_days <= 0:
            return
        cutoff = datetime.now() - timedelta(days=keep_days)
        for root in (self.data_dir() / "reports", self.data_dir() / "render"):
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                        path.unlink()
                except OSError:
                    continue

    # ------------------------------------------------------------ 发送

    async def send(
        self,
        stream_id: str,
        day: str = "",
        *,
        room_name: str = "",
        regenerate: bool = False,
    ) -> tuple[bool, str]:
        """生成（或复用已有）摘录并发送到聊天流。"""
        config = self._config()
        target_day = day.strip() or today_in_timezone(config.general.timezone)
        png_path, _, _ = self.report_paths(stream_id, target_day)

        # 重新生成时不先删旧图：generate(force=True) 写好新图后才替换，
        # 即使渲染或发送失败，历史里那张也还在。
        if regenerate or not png_path.exists():
            ok, message, path = await self.generate(
                stream_id, target_day, room_name=room_name, force=regenerate
            )
            if not ok or path is None:
                return False, message
            png_path = path

        from src.app.plugin_system.api.send_api import send_image

        payload = base64.b64encode(png_path.read_bytes()).decode("ascii")
        timeout = max(5, int(config.general.send_timeout_seconds))
        try:
            sent = await asyncio.wait_for(
                send_image(
                    payload,
                    stream_id=stream_id,
                    platform=config.general.platform,
                    processed_plain_text=f"{target_day} 群聊摘录",
                ),
                timeout=timeout,
            )
        except TimeoutError:
            logger.error(f"发送群聊摘录超时（{timeout}s）：stream={stream_id} day={target_day}")
            return False, f"摘录已生成，但发送超时（{timeout} 秒），适配器可能没响应"
        except Exception as exc:
            logger.error(f"发送群聊摘录失败：{exc}", exc_info=True)
            return False, f"摘录已生成，但发送失败：{exc}"
        if not sent:
            return False, "摘录已生成，但发送接口返回失败"
        return True, f"{target_day} 的摘录已发出"

    async def preview(self, stream_id: str, day: str = "", *, room_name: str = "") -> tuple[bool, str]:
        """只生成不发送，用于排版调试。"""
        ok, message, _ = await self.generate(stream_id, day, room_name=room_name)
        return ok, message

    # ------------------------------------------------------------ 定时辅助

    def is_due(self, now: datetime | None = None) -> bool:
        """判断当前是否已到配置的发送时间。"""
        config = self._config()
        tz = resolve_timezone(config.general.timezone)
        current = now.astimezone(tz) if now is not None else datetime.now(tz)
        try:
            hour, minute = (int(part) for part in config.general.send_time.strip().split(":", 1))
            target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except (ValueError, TypeError):
            logger.warning(f"发送时间配置非法：{config.general.send_time!r}，已跳过")
            return False
        return current >= target

    def report_day(self, now: datetime | None = None) -> str:
        """返回本次定时任务应该统计的日期。

        发送时间早于早上六点时，视为「补昨天的摘录」。
        """
        config = self._config()
        tz = resolve_timezone(config.general.timezone)
        current = now.astimezone(tz) if now is not None else datetime.now(tz)
        try:
            hour = int(config.general.send_time.strip().split(":", 1)[0])
        except (ValueError, TypeError):
            hour = 23
        target = current.date() if hour >= 6 else current.date() - timedelta(days=1)
        return target.isoformat()

    def group_room_names(self) -> dict[str, str]:
        """解析 ``群号=群名`` 形式的别名配置。"""
        config = self._config()
        result: dict[str, str] = {}
        for item in config.general.group_aliases:
            text = str(item or "").strip()
            if not text or "=" not in text:
                continue
            group_id, name = text.split("=", 1)
            group_id, name = group_id.strip(), name.strip()
            if group_id and name:
                result[group_id] = name
        return result
