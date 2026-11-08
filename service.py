"""群聊摘录生成与投递服务。

职责链：读取消息 → 统计 → LLM/规则文案 → 渲染手账长图 → 落盘 → 发送。
Service 不缓存跨调用状态，所有中间结果都写入 ``data/group_notebook``。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from datetime import date as date_cls
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseService

from .analyzer import analyze_day
from .dossier_analyzer import analyze_dossier
from .collector import (
    build_transcript,
    collect_dossier_pool,
    compute_stats,
    day_bounds,
    fetch_day_messages,
    fetch_range_messages,
    member_bot_stats,
    member_incoming_stats,
    member_interaction_stats,
    member_target_stats,
    monday_of,
    resolve_timezone,
    sample_messages,
    split_bot_messages,
    today_in_timezone,
)
from .config import GroupNotebookConfig
from .models import NotebookReport
from .renderer import NotebookRenderer
from .reports import build_stem, is_valid_day, ReportEntry, list_reports, slug_for_stream

logger = get_logger("group_notebook")

_VERSION = "0.6.4"


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

    def report_paths(self, stream_id: str, stem: str) -> tuple[Path, Path, Path]:
        """返回 (PNG 路径, JSON 路径, HTML 路径)；stem 为产物文件名（不含扩展名）。"""
        folder = self.report_dir(stream_id)
        html_folder = self.render_dir(stream_id)
        return (
            folder / f"{stem}.png",
            folder / f"{stem}.json",
            html_folder / f"{stem}.html",
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
        async with self._lock(f"{stream_id}:{target_day}"):
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
        message = f"{day} 的摘录已生成（{stats.message_count} 条消息，{engine_used} 引擎）"
        if report.source.startswith("fallback") and report.llm_error:
            message += (
                f"；⚠️ 模型调用失败（{report.llm_error}），本次为规则版文案——"
                "请检查 analysis.model_task 对应的模型服务，或换一个模型任务"
            )
        elif report.source.startswith("fallback"):
            message += "；⚠️ 本次为规则版文案（模型输出不可用）"
        return True, message, png_path

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

        sent, fail_reason = await self._send_image_with_retry(
            png_path, stream_id, caption=f"{target_day} 群聊摘录"
        )
        if not sent:
            logger.error(f"发送群聊摘录失败：{fail_reason}：stream={stream_id} day={target_day}")
            return False, f"摘录已生成，但{fail_reason}（已自动重试）"
        return True, f"{target_day} 的摘录已发出"

    async def preview(self, stream_id: str, day: str = "", *, room_name: str = "") -> tuple[bool, str]:
        """只生成不发送，用于排版调试。"""
        ok, message, _ = await self.generate(stream_id, day, room_name=room_name)
        return ok, message

    # ------------------------------------------------------------ 周报

    def week_title(self) -> str:
        """周记主标题：未配置时按机器人名自动生成。"""
        weekly = (self._config().notebook.weekly_page_title or "").strip()
        if weekly:
            return weekly
        bot_name = (self._config().notebook.bot_name or "").strip() or "安安"
        return f"{bot_name}的群聊周记"

    def normalize_period(self, start_day: str, end_day: str, *,
                         default_days: int = 7) -> tuple[str, str]:
        """归一化起止日期，返回 (start, end)。

        end 留空时取 start + default_days-1（不超过今天）；end 早于 start 时自动对调；
        超过今天的部分截断到今天。
        """
        today = today_in_timezone(self._config().general.timezone)
        start = (start_day or "").strip() or today
        if not is_valid_day(start):
            raise ValueError(f"起始日期格式不对：{start}（应为 YYYY-MM-DD）")
        if not end_day or not str(end_day).strip():
            end = start if default_days <= 1 else (
                date_cls.fromisoformat(start) + timedelta(days=default_days - 1)
            ).isoformat()
        else:
            end = str(end_day).strip()
            if not is_valid_day(end):
                raise ValueError(f"截止日期格式不对：{end}（应为 YYYY-MM-DD）")
        if end < start:
            start, end = end, start
        if start > today:
            raise ValueError("未来的记录还不存在，等那天过完再来")
        if end > today:
            end = today
        return start, end

    def period_text(self, start: str, end: str, suffix: str) -> str:
        """区间展示文本。"""
        if end != start:
            return f"{start} ~ {end[5:]} · {suffix}"
        return f"{start} · {suffix}"

    def normalize_week(self, week_monday: str) -> tuple[str, str, str]:
        """把任意日期归一化到所在自然周，返回 (周一, 周日, 展示文本)。"""

    async def generate_week(
        self,
        stream_id: str,
        start_day: str = "",
        end_day: str = "",
        *,
        room_name: str = "",
        force: bool = False,
    ) -> tuple[bool, str, Path | None]:
        """生成自选区间的手账周记（默认 start 所在自然周；未结束则统计到当前）。

        Args:
            start_day: 起始日期（留空为今天）。
            end_day: 截止日期（留空为 start 所在自然周的周日，不超过今天）。
        """
        config = self._config()
        try:
            start, end = self.normalize_period(start_day, end_day, default_days=7)
        except ValueError as exc:
            return False, str(exc), None
        period_text = self.period_text(start, end, "周记")
        stem = build_stem("week", start, end)
        png_path, json_path, html_path = self.report_paths(stream_id, stem)
        async with self._lock(f"week:{stream_id}:{stem}"):
            days = (date_cls.fromisoformat(end) - date_cls.fromisoformat(start)).days + 1
            messages = await fetch_range_messages(
                stream_id, start, days, config.general.timezone,
                include_bot=True, max_messages=0,
            )
            if not messages:
                return False, f"{start} ~ {end} 还没有聊天记录，周记没东西可写", None

            human_messages, bot_messages = await split_bot_messages(messages)
            stats_messages = messages if config.analysis.include_bot else human_messages
            stats = compute_stats(
                stats_messages,
                day=start,
                timezone_name=config.general.timezone,
                bot_messages=bot_messages,
            )
            transcript = build_transcript(
                sample_messages(messages, config.analysis.max_messages),
                timezone_name=config.general.timezone,
                per_message_limit=config.analysis.message_char_limit,
                budget=config.analysis.transcript_budget,
            )
            report = await analyze_day(
                report_date=start,
                room_name=self.room_name_for(stream_id, room_name),
                bot_name=config.notebook.bot_name,
                page_title=self.week_title(),
                stats=stats,
                messages=stats_messages,
                transcript=transcript,
                config=config,
                period_word="一周",
                period_text=period_text,
            )
            try:
                engine_used = await self._render_report(report, png_path, html_path)
            except Exception as exc:
                logger.error(f"渲染群聊周记失败：{exc}", exc_info=True)
                return False, f"周记渲染失败：{exc}", None
            try:
                await asyncio.to_thread(
                    self._save_report, png_path, json_path, report.to_dict()
                )
            except Exception as exc:
                logger.error(f"保存群聊周记失败：{exc}", exc_info=True)
                return False, f"周记保存失败：{exc}", None
            self._cleanup_old_reports()
            logger.info(
                f"群聊周记已生成：{png_path}（引擎 {engine_used}，来源 {report.source}，"
                f"消息 {stats.message_count} 条）"
            )
            message = (
                f"{period_text} 的周记已生成（{stats.message_count} 条消息，{engine_used} 引擎）"
            )
            if report.source.startswith("fallback") and report.llm_error:
                message += f"；⚠️ 模型调用失败（{report.llm_error}），本次为规则版文案"
            return True, message, png_path

    async def send_week(
        self,
        stream_id: str,
        start_day: str = "",
        end_day: str = "",
        *,
        room_name: str = "",
        regenerate: bool = False,
    ) -> tuple[bool, str]:
        """生成（或复用已有）周记并发送到聊天流。"""
        try:
            start, end = self.normalize_period(start_day, end_day, default_days=7)
        except ValueError as exc:
            return False, str(exc)
        png_path, _json, _html = self.report_paths(
            stream_id, build_stem("week", start, end)
        )
        if regenerate or not png_path.exists():
            ok, message, path = await self.generate_week(
                stream_id, start, end_day=end, room_name=room_name, force=regenerate
            )
            if not ok or path is None:
                return False, message
            png_path = path

        sent, fail_reason = await self._send_image_with_retry(
            png_path, stream_id, caption=f"{start} ~ {end} 群聊周记"
        )
        if not sent:
            logger.error(f"发送群聊周记失败：{fail_reason}：stream={stream_id} {start}~{end}")
            return False, f"周记已生成，但{fail_reason}（已自动重试）"
        return True, f"{start} ~ {end} 的周记已发出"

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

        发送时间早于早上六点时，视为「补昨天的日报」。
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

    # ------------------------------------------------------------ 专题案底

    def dossier_title(self) -> str:
        """案底专题名。"""
        return (self._config().dossier.title or "迫害记录簿").strip()

    def focus_word(self, direction: str = "member_as_culprit",
                   victim_display: str = "") -> str:
        """筛选焦点描述：按方向给默认值。"""
        cfg = self._config()
        focus = (cfg.dossier.focus_word or "").strip()
        if focus:
            return focus
        bot_name = (cfg.notebook.bot_name or "").strip() or "本bot"
        if direction == "member_as_victim" and victim_display:
            return f"迫害{victim_display}"
        return f"迫害{bot_name}"

    def case_number(self, monday: str) -> str:
        """案卷编号：MOFOX-<年>-W<ISO 周序号>。"""
        try:
            iso = date_cls.fromisoformat(monday).isocalendar()
            return f"MOFOX-{iso.year}-W{iso.week:02d}"
        except ValueError:
            return f"MOFOX-{monday}"

    def dossier_paths(self, stream_id: str, stem: str) -> tuple[Path, Path, Path]:
        """案底产物路径（PNG/JSON/HTML），kind=dossier。"""
        return self.report_paths(stream_id, stem)

    @staticmethod
    def member_name_of(messages: list[dict[str, Any]], member_id: str) -> str:
        from .models import clean_text

        for message in messages:
            if str(message.get("sender_id") or "") == member_id:
                return clean_text(message.get("sender_name") or member_id, 24)
        return member_id

    def member_name(self, messages: list[dict[str, Any]], member_id: str) -> str:
        """从消息列表里解析成员当前昵称。"""
        from .models import clean_text

        for message in messages:
            if str(message.get("sender_id") or "") == member_id:
                return clean_text(message.get("sender_name") or member_id, 24)
        return member_id

    async def generate_dossier(
        self,
        stream_id: str,
        start_day: str = "",
        end_day: str = "",
        *,
        room_name: str = "",
        members: list[str] | None = None,
        direction: str = "",
        force: bool = False,
    ) -> tuple[bool, str, Path | None]:
        """生成自选区间的案件卷宗（聚焦成员留空走自动 Top N）。

        Args:
            start_day: 起始日期（留空为今天）。
            end_day: 截止日期（留空与 start 同日，即单日卷宗）。
            members: 聚焦成员的 QQ 号列表；为空时自动取方向榜 Top N。
            direction: ``member_as_culprit``（成员迫害其他成员）/ ``member_as_victim``
                （成员被围攻）；留空用配置默认。
        """
        config = self._config()
        direction = (direction or config.dossier.default_direction or
                     "member_as_culprit").strip()
        if direction not in {"member_as_culprit", "member_as_victim"}:
            direction = "member_as_culprit"
        try:
            start, end = self.normalize_period(start_day, end_day, default_days=1)
        except ValueError as exc:
            return False, str(exc), None
        focus_key = hashlib.md5(
            f"{direction}|{','.join(sorted(str(m) for m in (members or []))) or 'auto'}".encode("utf-8")
        ).hexdigest()[:8]
        stem = build_stem("dossier", start, end, key=focus_key)
        period_text = self.period_text(start, end, "案卷")
        png_path, json_path, html_path = self.dossier_paths(stream_id, stem)
        async with self._lock(f"dossier:{stream_id}:{stem}"):
            days = (date_cls.fromisoformat(end) - date_cls.fromisoformat(start)).days + 1
            messages = await fetch_range_messages(
                stream_id, start, days, config.general.timezone,
                include_bot=True, max_messages=0,
            )
            if not messages:
                return False, f"{start} ~ {end} 没有聊天记录，案底无从查起", None

            human_messages, bot_messages = await split_bot_messages(messages)
            bot_ids = {
                str(message.get("sender_id") or "")
                for message in bot_messages
                if str(message.get("sender_id") or "")
            }

            all_member_ids = {
                str(message.get("sender_id") or "")
                for message in messages
                if str(message.get("sender_id") or "")
            }
            if direction == "member_as_victim":
                # 成员被迫害：受害者=聚焦成员（留空=被迫害最多的 Top N）
                target_stats = member_target_stats(messages)
                if members:
                    focus_ids = [str(m).strip() for m in members if str(m).strip()]
                    missing = [mid for mid in focus_ids if mid not in target_stats]
                    if missing:
                        return False, (
                            "这些成员本周没有被指向的记录或不存在："
                            + "、".join(missing)
                        ), None
                else:
                    focus_ids = [
                        victim for victim, _info in sorted(
                            target_stats.items(), key=lambda item: item[1]["count"],
                            reverse=True,
                        )[: max(1, int(config.dossier.top_members))]
                    ]
                victim_names = {
                    mid: self.member_name(messages, mid) for mid in focus_ids
                }
                culprit_stats = [
                    {"name": info["name"], "count": info["count"]}
                    for _mid, info in sorted(
                        member_incoming_stats(
                            messages, victim_ids=set(focus_ids),
                            victim_names=victim_names,
                        ).items(), key=lambda item: item[1]["count"], reverse=True,
                    )[: max(1, int(config.dossier.top_members) * 2)]
                ]
                victim_display = "、".join(victim_names[mid] for mid in focus_ids)
                pool = collect_dossier_pool(
                    messages, bot_ids=bot_ids,
                    bot_name=config.notebook.bot_name,
                    direction="member_as_victim",
                    focus_members=focus_ids,
                    victim_names=victim_names,
                )
            else:
                # 成员迫害其他成员：主犯=聚焦成员（留空=对他人互动 Top N），
                # 受害者逐案标注（其他群友或各 bot 都算）
                interact_stats = member_interaction_stats(
                    messages, member_ids=(
                        {str(m).strip() for m in members if str(m).strip()}
                        if members else None
                    )
                )
                focus_ids = [
                    sender for sender, _info in sorted(
                        interact_stats.items(), key=lambda item: item[1]["count"],
                        reverse=True,
                    )[: max(1, int(config.dossier.top_members))]
                ]
                if not focus_ids:
                    return False, "这一周没有找到聚焦成员的互动记录", None
                culprit_stats = [
                    {"name": self.member_name_of(messages, mid),
                     "count": interact_stats[mid]["count"]}
                    for mid in focus_ids
                ]
                victim_display = ""
                pool = collect_dossier_pool(
                    messages, bot_ids=bot_ids,
                    bot_name=config.notebook.bot_name,
                    direction="member_as_culprit",
                    focus_members=focus_ids,
                )
            if not pool:
                return False, "候选池为空：这一周大家都很乖，没有可立案的记录", None

            case_no = self.case_number(start)
            focus_word = (
                self.focus_word(direction, victim_display)
                if direction == "member_as_victim" else self.focus_word(direction)
            )
            dossier = await analyze_dossier(
                report_date=start,
                room_name=self.room_name_for(stream_id, room_name),
                bot_name=config.notebook.bot_name,
                page_title=self.dossier_title(),
                period_text=period_text,
                case_no=case_no,
                focus_word=focus_word,
                pool_messages=pool,
                culprit_stats=culprit_stats,
                max_cases=max(1, int(config.dossier.max_cases)),
                top_members=max(1, int(config.dossier.top_members)),
                config=config,
                direction=direction,
                victim_display=victim_display,
            )
            try:
                engine_used = await self._render_dossier(dossier, png_path, html_path)
            except Exception as exc:
                logger.error(f"渲染案底失败：{exc}", exc_info=True)
                return False, f"案底渲染失败：{exc}", None
            try:
                await asyncio.to_thread(
                    self._save_report, png_path, json_path, dossier.to_dict()
                )
            except Exception as exc:
                logger.error(f"保存案底失败：{exc}", exc_info=True)
                return False, f"案底保存失败：{exc}", None
            self._cleanup_old_reports()
            logger.info(
                f"群聊案底已生成：{png_path}（引擎 {engine_used}，方向 {direction}，"
                f"来源 {dossier.source}，案件 {len(dossier.cases)} 起）"
            )
            message = (
                f"{period_text} 的案底已生成（{len(dossier.cases)} 起案件，{engine_used} 引擎）"
            )
            if dossier.source.startswith("fallback") and dossier.llm_error:
                message += f"；⚠️ 模型调用失败（{dossier.llm_error}），本次为规则版卷宗"
            return True, message, png_path

    async def send_dossier(
        self,
        stream_id: str,
        start_day: str = "",
        end_day: str = "",
        *,
        room_name: str = "",
        members: list[str] | None = None,
        direction: str = "",
        regenerate: bool = False,
    ) -> tuple[bool, str]:
        """生成（或复用已有）案底并发送到聊天流。"""
        try:
            start, end = self.normalize_period(start_day, end_day, default_days=1)
        except ValueError as exc:
            return False, str(exc)
        png_path, _json, _html = self.dossier_paths(
            stream_id, build_stem("dossier", start, end, key="")
        )
        if regenerate or not png_path.exists():
            ok, message, path = await self.generate_dossier(
                stream_id, start, end_day=end, room_name=room_name,
                members=members, direction=direction, force=regenerate,
            )
            if not ok or path is None:
                return False, message
            png_path = path

        sent, fail_reason = await self._send_image_with_retry(
            png_path, stream_id, caption=f"{start} ~ {end} {self.dossier_title()}"
        )
        if not sent:
            logger.error(f"发送案底失败：{fail_reason}：stream={stream_id} {start}~{end}")
            return False, f"案底已生成，但{fail_reason}（已自动重试）"
        return True, f"{start} ~ {end} 的案底已发出"

    async def _render_dossier(self, report: Any, png_path: Path, html_path: Path) -> str:
        """按配置选择案底渲染引擎（与手账相同的 auto 回退逻辑）。"""
        from .renderer import pick_engine

        engine = pick_engine(self._config().notebook)
        if engine == "playwright":
            try:
                await self.renderer().render_dossier(report, png_path, html_path)
                return "playwright"
            except Exception as exc:
                if str(self._config().notebook.render_engine or "auto").strip().lower() == "playwright":
                    raise
                logger.warning(f"Playwright 案底渲染失败（{exc}），自动回退 Pillow 引擎")
        pillow = self.pillow_renderer()
        await asyncio.to_thread(pillow.render_dossier, report, png_path)
        return "pillow"

    async def _send_image_with_retry(
        self,
        png_path: Path,
        stream_id: str,
        *,
        caption: str,
        attempts: int = 3,
    ) -> tuple[bool, str]:
        """发送图片，失败自动重试（QQ 连接瞬断时自愈）。

        Returns:
            ``(是否成功, 失败说明)``——全部失败时才返回 False。
        """
        from src.app.plugin_system.api.send_api import send_image

        config = self._config()
        payload = base64.b64encode(png_path.read_bytes()).decode("ascii")
        timeout = max(5, int(config.general.send_timeout_seconds))
        last_error = ""
        for attempt in range(1, max(1, attempts) + 1):
            try:
                sent = await asyncio.wait_for(
                    send_image(
                        payload,
                        stream_id=stream_id,
                        platform=config.general.platform,
                        processed_plain_text=caption,
                    ),
                    timeout=timeout,
                )
            except TimeoutError:
                last_error = f"发送超时（{timeout} 秒）"
            except Exception as exc:
                last_error = f"发送失败：{exc}"
            else:
                if sent:
                    return True, ""
                last_error = "发送接口返回失败"
            if attempt < attempts:
                logger.warning(f"第 {attempt} 次发送未成功（{last_error}），6 秒后重试")
                await asyncio.sleep(6)
        return False, last_error

    # ------------------------------------------------------------ 周报定时辅助

    def is_week_due(self, now: datetime | None = None) -> bool:
        """判断当前是否到达周报投递时间（指定星期 + 时刻）。"""
        config = self._config()
        tz = resolve_timezone(config.general.timezone)
        current = now.astimezone(tz) if now is not None else datetime.now(tz)
        try:
            send_day = max(0, min(6, int(config.general.weekly_send_day)))
        except (TypeError, ValueError):
            return False
        if current.weekday() != send_day:
            return False
        try:
            hour, minute = (
                int(part) for part in config.general.weekly_send_time.strip().split(":", 1)
            )
            target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except (ValueError, TypeError):
            logger.warning(f"周报发送时间配置非法：{config.general.weekly_send_time!r}，已跳过")
            return False
        return current >= target

    def current_week_monday(self, now: datetime | None = None) -> str:
        """返回当前时刻所在自然周的周一日期。"""
        config = self._config()
        tz = resolve_timezone(config.general.timezone)
        current = now.astimezone(tz) if now is not None else datetime.now(tz)
        return monday_of(current.date().isoformat())

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
