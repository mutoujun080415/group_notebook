"""手账模板渲染：Jinja2 填充 HTML + Playwright 截图成 PNG。

浏览器实例按插件生命周期复用（惰性启动、卸载时关闭）；模板上下文里
所有富文本（[昵称] 胶囊）都在 Python 侧转义后生成 HTML 片段，模板本身
不做二次转义处理。
"""

from __future__ import annotations

import asyncio
import html as html_module
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import NotebookReport

# 手账主题的 8 色调色板（与聊天天气维度条共用）。
PALETTE = ["#ffb74d", "#ff8a65", "#ba68c8", "#4db6ac", "#4dd0e1", "#64b5f6", "#81c784", "#e57373"]

_MENTION_RE = re.compile(r"\[([^\[\]]{1,24})\]")
_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

_LAUNCH_ARGS = [
    "--allow-file-access-from-files",
    "--force-color-profile=srgb",
    "--font-render-hinting=none",
    "--disable-dev-shm-usage",
]

_MAX_PAGE_WIDTH = 800


def color_for(name: str) -> str:
    """按昵称稳定取一个调色板颜色。"""
    return PALETTE[sum(ord(char) for char in (name or "?")) % len(PALETTE)]


def initial_for(name: str) -> str:
    """取昵称的首个可见字符做圆形头像文字。"""
    for char in (name or "?"):
        if not char.isspace():
            return char
    return "?"


def _resolve_mention(raw: str, names: list[str]) -> str:
    """把方括号里的昵称对齐到真实昵称，失败返回空串。"""
    if raw in names:
        return raw
    normalized = re.sub(r"\s+", "", raw)
    for candidate in names:
        if re.sub(r"\s+", "", candidate) == normalized:
            return candidate
    return ""


def rich_html(text: str, names: list[str]) -> str:
    """把含 ``[昵称]`` 的文本转成转义后的 HTML，昵称渲染成 @胶囊。"""
    result: list[str] = []
    last = 0
    for match in _MENTION_RE.finditer(text or ""):
        result.append(html_module.escape(text[last : match.start()]))
        resolved = _resolve_mention(match.group(1), names)
        if resolved:
            result.append(f'<span class="mention">@{html_module.escape(resolved)}</span>')
        else:
            result.append(html_module.escape(match.group(0)))
        last = match.end()
    result.append(html_module.escape(text[last:]))
    return "".join(result)


def plain_html(text: str) -> str:
    """转义纯文本。"""
    return html_module.escape(str(text or ""))


class _BrowserHolder:
    """跨渲染复用的 Playwright 浏览器单例。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._pw: Any = None
        self._browser: Any = None
        self._channel = ""

    async def acquire(self, channel: str) -> Any:
        """返回可用的浏览器实例；按需启动或切换通道。"""
        async with self._lock:
            if self._browser is not None and self._channel == channel:
                try:
                    if self._browser.is_connected():
                        return self._browser
                except Exception:
                    pass
            await self._close()
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            errors: list[str] = []
            if channel:
                try:
                    self._browser = await self._pw.chromium.launch(
                        channel=channel, headless=True, args=_LAUNCH_ARGS
                    )
                    self._channel = channel
                    return self._browser
                except Exception as exc:
                    errors.append(f"通道 {channel}: {exc}")
            try:
                self._browser = await self._pw.chromium.launch(
                    headless=True, args=_LAUNCH_ARGS
                )
                self._channel = ""
                return self._browser
            except Exception as exc:
                errors.append("内置 chromium: " + str(exc))
                await self._close()
                raise RuntimeError(
                    "无法启动截图浏览器（" + "；".join(errors) + "）。"
                    "可以先运行 `playwright install chromium` 安装内置浏览器，"
                    "或把 notebook.browser_channel 配置为 msedge / chrome 复用系统浏览器。"
                ) from None

    async def _close(self) -> None:
        """关闭浏览器与驱动（不抛错）。"""
        try:
            if self._browser is not None:
                await self._browser.close()
        except Exception:
            pass
        self._browser = None
        self._channel = ""
        try:
            if self._pw is not None:
                await self._pw.stop()
        except Exception:
            pass
        self._pw = None

    async def shutdown(self) -> None:
        """对外关闭入口。"""
        async with self._lock:
            await self._close()


_holder = _BrowserHolder()


async def shutdown_browser() -> None:
    """插件卸载时关闭共享浏览器。"""
    await _holder.shutdown()


def fonts_css(base_dir: Path) -> str:
    """扫描插件自带字体并生成 @font-face 样式；缺失时由 CSS 字体栈兜底。"""
    fonts_dir = base_dir / "assets" / "fonts"
    faces: list[str] = []

    def _face(family: str, file_name: str, weight: str = "400") -> str:
        path = fonts_dir / file_name
        if not path.is_file():
            return ""
        return (
            f"@font-face {{ font-family: '{family}'; "
            f"src: url('{path.as_uri()}') format('truetype'); "
            f"font-weight: {weight}; font-display: swap; }}"
        )

    faces.append(_face("ZCOOL KuaiLe", "ZCOOLKuaiLe-Regular.ttf"))
    faces.append(_face("LXGW WenKai", "LXGWWenKai-Regular.ttf"))
    faces.append(_face("LXGW WenKai", "LXGWWenKai-Medium.ttf", weight="700"))
    css = "\n".join(face for face in faces if face)
    return f"<style>\n{css}\n</style>" if css else ""


def character_uri(config: Any, base_dir: Path) -> str:
    """解析立绘路径为 file URI；不存在返回空串。"""
    raw = str(getattr(config.notebook, "character_path", "") or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if not path.is_absolute():
        path = base_dir / path
    try:
        resolved = path.resolve()
    except OSError:
        return ""
    if not resolved.is_file():
        return ""
    try:
        return resolved.as_uri()
    except (OSError, ValueError):
        return ""


class NotebookRenderer:
    """把 :class:`NotebookReport` 渲染成手账长图。"""

    def __init__(self, config: Any, base_dir: Path) -> None:
        self._config = config
        self._base_dir = Path(base_dir).resolve()
        self._env = None

    # ------------------------------------------------------------ 上下文

    def build_context(self, report: NotebookReport) -> dict[str, Any]:
        """把报告数据组装成模板上下文（纯函数，便于测试）。"""
        notebook = self._config.notebook
        known_names: list[str] = []
        for pool in (
            [item["name"] for item in report.stats.top_members],
            [item.name for item in report.portraits],
            [item.sender for item in report.quotes],
            [name for topic in report.topics for name in topic.contributors],
        ):
            for name in pool:
                if name and name not in known_names:
                    known_names.append(name)

        peak = max(range(24), key=lambda index: report.stats.hourly[index])
        peak_count = max(1, report.stats.hourly[peak])
        max_count = max(report.stats.hourly) or 1
        hourly = [
            {
                "hour": hour,
                "label": f"{hour:02d}",
                "count": report.stats.hourly[hour],
                "height": round(report.stats.hourly[hour] * 100 / max_count, 1),
                "peak": hour == peak and report.stats.hourly[hour] > 0,
            }
            for hour in range(24)
        ]

        topics = []
        for index, topic in enumerate(report.topics):
            topics.append(
                {
                    "index": index + 1,
                    "title": topic.title,
                    "title_hl": "pink" if index % 3 == 0 else ("blue" if index % 3 == 1 else "green"),
                    "contributors": [
                        {"name": name, "color": color_for(name), "initial": initial_for(name)}
                        for name in topic.contributors
                    ],
                    "detail_html": rich_html(topic.detail, known_names),
                }
            )

        portraits = []
        for index, portrait in enumerate(report.portraits):
            portraits.append(
                {
                    **portrait.to_dict(),
                    "color": color_for(portrait.name),
                    "initial": initial_for(portrait.name),
                    "tilt": ("-1deg", "1.2deg", "-0.6deg", "0.8deg", "-1.4deg", "0.5deg")[index % 6],
                }
            )

        quotes = []
        for index, quote in enumerate(report.quotes):
            quotes.append(
                {
                    "sender": quote.sender,
                    "text": plain_html(quote.text),
                    "comment_html": rich_html(quote.comment, known_names),
                    "color": color_for(quote.sender),
                    "initial": initial_for(quote.sender),
                    "alt": index % 2 == 1,
                }
            )

        mood = report.mood
        dims = list(mood.dimensions)
        bar = [
            {
                "name": dim.name,
                "pct": dim.percentage,
                "color": PALETTE[index % len(PALETTE)],
            }
            for index, dim in enumerate(dims)
            if dim.percentage > 0
        ]
        mood_dims = [
            {
                **dim.to_dict(),
                "comment_html": rich_html(dim.comment, []),
                "color": PALETTE[index % len(PALETTE)],
                "tilt": ("-1deg", "1.5deg", "-0.5deg", "1deg", "-1.5deg", "0.5deg")[index % 6],
            }
            for index, dim in enumerate(dims)
        ]

        try:
            date_obj = datetime.fromisoformat(report.date)
        except ValueError:
            date_obj = None
        date_text = (
            f"{date_obj.year} 年 {date_obj.month} 月 {date_obj.day} 日 · {_WEEKDAYS[date_obj.weekday()]}"
            if date_obj
            else report.date
        )
        generated_text = str(report.generated_at or "").replace("T", " ")

        return {
            "page_title": report.page_title,
            "brand_text": notebook.brand_text,
            "stamp_text": notebook.stamp_text,
            "bot_name": report.bot_name,
            "room_name": report.room_name,
            "greeting": plain_html(report.greeting),
            "date_text": date_text,
            "generated_at": generated_text,
            "source": report.source,
            "stats": report.stats.to_dict(),
            "peak_text": f"{peak:02d}:00 - {(peak + 1) % 24:02d}:00",
            "hourly": hourly,
            "topics": topics,
            "topic_note": f"本页摘抄了 {len(topics)} 个话题",
            "recap": {
                "enabled": bool(notebook.recap_enabled and report.recap),
                "title": notebook.recap_section_title,
                "text": rich_html(report.recap, known_names),
                "note": "把今天的纸页合成一段话",
            },
            "portraits": portraits,
            "people_note": f"本页一共画了 {len(portraits)} 位群友",
            "quotes": quotes,
            "quote_note": f"从今天的聊天里挑了 {len(quotes)} 句",
            "mood": {
                "title": mood.title,
                "subtitle": mood.subtitle,
                "bar": bar,
                "dims": mood_dims,
                "summary": plain_html(mood.summary),
                "has_dims": bool(mood_dims),
            },
            "coffee": {
                "enabled": bool(notebook.coffee_enabled and report.coffee),
                "title": f"{report.bot_name}的咖啡推荐",
                "text": plain_html(report.coffee),
            },
            "murmur": {
                "enabled": bool(notebook.murmur_enabled and report.murmur),
                "title": f"{report.bot_name}的碎碎念",
                "text": plain_html(report.murmur),
            },
            "section": {
                "time": notebook.time_section_title,
                "topic": notebook.topic_section_title,
                "people": notebook.people_section_title,
                "quote": notebook.quote_section_title,
                "mood": notebook.mood_section_title,
            },
            "footer": {
                "brand": notebook.brand_text,
                "note": "Neo-MoFox · 群聊摘录插件",
                "version": "v0.1.0",
            },
            "character_uri": character_uri(self._config, self._base_dir),
            "fonts_css": fonts_css(self._base_dir),
        }

    # ------------------------------------------------------------ HTML

    def _template(self) -> Any:
        """惰性创建 Jinja2 环境（依赖缺失时给出可读报错）。"""
        if self._env is None:
            try:
                from jinja2 import Environment, FileSystemLoader, select_autoescape
            except ImportError as exc:  # pragma: no cover - 依赖声明兜底
                raise RuntimeError(
                    "缺少 jinja2 依赖，请先安装：uv add jinja2（或 pip install jinja2）"
                ) from exc
            self._env = Environment(
                loader=FileSystemLoader(str(self._base_dir / "templates")),
                autoescape=select_autoescape(default=True),
                trim_blocks=True,
                lstrip_blocks=True,
            )
        return self._env.get_template("notebook.html")

    def render_html(self, report: NotebookReport) -> str:
        """填充模板返回 HTML 字符串。"""
        template = self._template()
        return template.render(**self.build_context(report))

    # ------------------------------------------------------------ 截图

    async def render(
        self,
        report: NotebookReport,
        png_path: Path,
        html_path: Path,
    ) -> tuple[int, int]:
        """渲染 HTML 并截图保存 PNG，返回 (宽, 高)。"""
        html_text = self.render_html(report)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html_text, encoding="utf-8")

        notebook = self._config.notebook
        channel = (notebook.browser_channel or "").strip().lower()
        if channel in {"none", "off", "chromium"}:
            channel = ""
        scale = max(1, min(3, int(notebook.viewport_scale or 2)))
        timeout = max(15.0, float(notebook.render_timeout_seconds or 90))

        browser = await _holder.acquire(channel)
        page = await browser.new_page(
            viewport={"width": _MAX_PAGE_WIDTH, "height": 1000},
            device_scale_factor=scale,
        )
        try:
            await page.goto(html_path.as_uri(), wait_until="load")
            try:
                await page.evaluate("document.fonts.ready")
            except Exception:
                pass
            await page.wait_for_timeout(300)
            # 空白页保险：正文高度过低说明模板渲染出了问题，直接报错而不是存一张白图。
            body_height = await page.evaluate("document.body.scrollHeight")
            if not isinstance(body_height, (int, float)) or body_height < 600:
                raise RuntimeError(
                    f"手账页面渲染异常（正文高度仅 {body_height}px），"
                    f"可打开中间文件排查：{html_path}"
                )
            await asyncio.wait_for(
                page.screenshot(path=str(png_path), full_page=True, type="png"),
                timeout=timeout,
            )
        finally:
            try:
                await page.close()
            except Exception:
                pass
        if not png_path.is_file() or png_path.stat().st_size == 0:
            raise RuntimeError(f"截图未写出有效 PNG：{png_path}")
        return (_MAX_PAGE_WIDTH * scale, int(body_height or 0))

    async def shutdown(self) -> None:
        """关闭共享浏览器。"""
        await shutdown_browser()
