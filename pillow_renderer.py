"""Pillow 手账渲染器（备用引擎）。

与 HTML 引擎（renderer.py）输出同一张手账的等价版式，但不依赖浏览器——
Pillow 是 Neo-MoFox 的核心自带依赖，任何设备开箱即用。

画布方案：750 逻辑像素宽 + 2 倍超采样。所有绘制通过 :class:`Canvas` 进行，
坐标一律使用逻辑像素，由 Canvas 统一乘以缩放系数，避免混用导致的错位。
"""

from __future__ import annotations

import re
from datetime import datetime as _dt
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from . import cover
from .models import NotebookReport

SCALE = 2
PAGE_W = 750
MARGIN = 20
CARD_PAD = 24
CONTENT_W = PAGE_W - MARGIN * 2 - CARD_PAD * 2  # 662

INK = (93, 64, 55)
INK2 = (141, 110, 99)
INK3 = (161, 136, 127)
INK_FAINT = (188, 170, 164)
PAPER = (253, 251, 247)
CARD_BG = (255, 253, 248)
YELLOW = (255, 249, 196)
PINK = (255, 204, 188)
BLUE = (179, 229, 252)
GREEN = (200, 230, 201)
PURPLE = (225, 190, 231)
PURPLE_DEEP = (149, 117, 205)
ORANGE = (255, 112, 67)
TITLE_BG = (255, 253, 231)
TITLE_BORDER = (255, 183, 77)
TITLE_TEXT = (191, 54, 12)

PALETTE = ["#ffb74d", "#ff8a65", "#ba68c8", "#4db6ac", "#4dd0e1", "#64b5f6", "#81c784", "#e57373"]
PALETTE_RGB = [
    (255, 183, 77), (255, 138, 101), (186, 104, 200), (77, 182, 172),
    (77, 208, 225), (100, 181, 246), (129, 199, 132), (229, 115, 115),
]
MBTI_BG = (237, 231, 246)
MBTI_BORDER = PURPLE_DEEP
MBTI_TEXT = (81, 45, 168)

_HL_COLORS = {"pink": PINK, "blue": BLUE, "green": GREEN}
_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
_EMOJI_RE = re.compile(
    "[" "\U0001f300-\U0001faff" "\U00002600-\U000027bf" "\U0001f000-\U0001f2ff" "\u2b00-\u2bff" "]+"
)


def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def strip_emoji(text: str) -> str:
    """Pillow 字体不含彩色 emoji，直接剔除避免豆腐块。"""
    return _EMOJI_RE.sub("", str(text or "")).strip()


def color_for(name: str) -> tuple[int, int, int]:
    return PALETTE_RGB[sum(ord(ch) for ch in (name or "?")) % len(PALETTE_RGB)]


class _Fonts:
    """字体加载与缓存：优先插件自带字体，缺失回退系统字体。"""

    def __init__(self, base_dir: Path) -> None:
        self._base = Path(base_dir)
        self._cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
        self._paths: dict[str, tuple[tuple[str, ...], int]] = {
            "title": (
                (
                    str(self._base / "assets" / "fonts" / "ZCOOLKuaiLe-Regular.ttf"),
                    "C:/Windows/Fonts/msyh.ttc",
                    "C:/Windows/Fonts/msyh.ttf",
                ),
                0,
            ),
            "hand": (
                (
                    str(self._base / "assets" / "fonts" / "LXGWWenKai-Regular.ttf"),
                    "C:/Windows/Fonts/kaiTi.ttf",
                    "C:/Windows/Fonts/msyh.ttc",
                ),
                0,
            ),
            "bold": (
                (
                    str(self._base / "assets" / "fonts" / "LXGWWenKai-Medium.ttf"),
                    "C:/Windows/Fonts/msyhbd.ttc",
                    "C:/Windows/Fonts/msyh.ttc",
                ),
                0,
            ),
            "body": (
                (
                    str(self._base / "assets" / "fonts" / "LXGWWenKai-Regular.ttf"),
                    "C:/Windows/Fonts/msyh.ttc",
                    "C:/Windows/Fonts/simhei.ttf",
                ),
                0,
            ),
        }

    def get(self, kind: str, size: int) -> ImageFont.FreeTypeFont:
        key = (kind, size)
        if key not in self._cache:
            candidates, index = self._paths.get(kind, self._paths["body"])
            font = None
            for candidate in candidates:
                try:
                    font = ImageFont.truetype(candidate, size, index=index)
                    break
                except OSError:
                    continue
            if font is None:
                font = ImageFont.load_default(size)
            self._cache[key] = font
        return self._cache[key]


class Canvas:
    """逻辑坐标画布：内部画布为 scale 倍大小，坐标/宽度/字号统一换算。"""

    def __init__(self, fonts: _Fonts, w: int, h: int, scale: int = SCALE) -> None:
        self.fonts = fonts
        self.w = w
        self.h = h
        self.s = scale
        self.image = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
        self.draw = ImageDraw.Draw(self.image)

    # ---- 坐标换算 ----
    def _box(self, box: tuple[float, float, float, float]) -> tuple[float, ...]:
        return tuple(v * self.s for v in box)

    # ---- 图元 ----
    def rr(self, box, radius: int, fill=None, outline=None, width: int = 1) -> None:
        self.draw.rounded_rectangle(self._box(box), radius=radius * self.s,
                                    fill=fill, outline=outline,
                                    width=max(1, int(width * self.s)))

    def rect(self, box, fill=None, outline=None, width: int = 1) -> None:
        self.draw.rectangle(self._box(box), fill=fill, outline=outline,
                            width=max(1, int(width * self.s)))

    def line(self, pts, fill, width: int = 1) -> None:
        scaled = [v * self.s for pt in pts for v in pt]
        self.draw.line(scaled, fill=fill, width=max(1, int(width * self.s)))

    def ellipse(self, box, fill=None, outline=None, width: int = 1) -> None:
        self.draw.ellipse(self._box(box), fill=fill, outline=outline,
                          width=max(1, int(width * self.s)))

    def dashed_rr(self, box, radius: int, color, width: int = 2,
                  dash: int = 7, gap: int = 5) -> None:
        x0, y0, x1, y1 = box
        for yy in range(int(y0 + radius), int(y1 - radius), dash + gap):
            self.line([(x0, yy), (x0, min(yy + dash, y1 - radius))], color, width)
            self.line([(x1, yy), (x1, min(yy + dash, y1 - radius))], color, width)
        for xx in range(int(x0 + radius), int(x1 - radius), dash + gap):
            self.line([(xx, y0), (min(xx + dash, x1 - radius), y0)], color, width)
            self.line([(xx, y1), (min(xx + dash, x1 - radius), y1)], color, width)
        self.draw.arc(self._box((x0, y0, x0 + radius * 2, y0 + radius * 2)), 180, 270,
                      fill=color, width=max(1, int(width * self.s)))
        self.draw.arc(self._box((x1 - radius * 2, y0, x1, y0 + radius * 2)), 270, 360,
                      fill=color, width=max(1, int(width * self.s)))
        self.draw.arc(self._box((x0, y1 - radius * 2, x0 + radius * 2, y1)), 90, 180,
                      fill=color, width=max(1, int(width * self.s)))
        self.draw.arc(self._box((x1 - radius * 2, y1 - radius * 2, x1, y1)), 0, 90,
                      fill=color, width=max(1, int(width * self.s)))

    # ---- 文本 ----
    def text(self, xy, text: str, kind: str, size: int, fill) -> int:
        font = self.fonts.get(kind, size * self.s)
        self.draw.text((xy[0] * self.s, xy[1] * self.s), text, font=font, fill=fill)
        return int(font.getlength(text) / self.s)

    def tw(self, text: str, kind: str, size: int) -> int:
        return int(self.fonts.get(kind, size * self.s).getlength(text) / self.s)

    def wrap(self, text: str, kind: str, size: int, max_w: int) -> list[str]:
        font = self.fonts.get(kind, size * self.s)
        unit = max(1, int(font.getlength("字") / self.s))
        lines: list[str] = []
        current = ""
        for char in str(text or ""):
            if char == "\n":
                lines.append(current)
                current = ""
                continue
            if (len(current) + 1) * unit > max_w and current:
                lines.append(current)
                current = char
            else:
                current += char
        lines.append(current)
        return lines or [""]

    # ---- 图层 ----
    def paste_canvas(self, other: "Canvas", xy) -> None:
        self.image.alpha_composite(other.image, (int(xy[0] * self.s), int(xy[1] * self.s)))

    def paste_image(self, img: Image.Image, xy) -> None:
        self.image.alpha_composite(img, (int(xy[0] * self.s), int(xy[1] * self.s)))

    def paste_masked(self, img: Image.Image, xy, mask: Image.Image) -> None:
        self.image.paste(img, (int(xy[0] * self.s), int(xy[1] * self.s)), mask)

    def rotated(self, angle: float) -> Image.Image:
        return self.image.rotate(angle, expand=True, resample=Image.BICUBIC)


def _split_mentions(text: str, names: list[str]) -> list[tuple[str, str]]:
    """把文本切成 ('t', 文本) / ('m', 昵称) 片段；昵称需能对齐到真实昵称。"""
    segments: list[tuple[str, str]] = []
    last = 0

    def resolve(raw: str) -> str:
        if raw in names:
            return raw
        normalized = re.sub(r"\s+", "", raw)
        for candidate in names:
            if re.sub(r"\s+", "", candidate) == normalized:
                return candidate
        return ""

    for match in re.finditer(r"\[([^\[\]]{1,24})\]", text or ""):
        if match.start() > last:
            segments.append(("t", text[last : match.start()]))
        resolved = resolve(match.group(1))
        if resolved:
            segments.append(("m", resolved))
        else:
            segments.append(("t", match.group(0)))
        last = match.end()
    if last < len(text or ""):
        segments.append(("t", text[last:]))
    return segments


class PillowNotebookRenderer:
    """用 Pillow 直接绘制手账长图。"""

    def __init__(self, config: Any, base_dir: Path) -> None:
        self._config = config
        self._base = Path(base_dir).resolve()
        self._fonts = _Fonts(self._base)

    # ------------------------------------------------------------ 入口

    def render(self, report: NotebookReport, png_path: Path) -> tuple[int, int]:
        """渲染手账并保存 PNG，返回 (宽, 高)。"""
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

        inner = Canvas(self._fonts, CONTENT_W, 6000)
        y = 0.0
        y = self._draw_header(inner, y, report, notebook)
        y = self._draw_stats(inner, y + 16, report)
        y = self._draw_time(inner, y + 24, report, notebook)
        y = self._draw_topics(inner, y + 24, report, notebook, known_names)
        y = self._draw_recap(inner, y + 24, report, notebook)
        y = self._draw_people(inner, y + 24, report, notebook)
        y = self._draw_quotes(inner, y + 24, report, notebook)
        y = self._draw_mood(inner, y + 24, report, notebook)
        y = self._draw_coffee(inner, y + 20, report, notebook)
        y = self._draw_murmur(inner, y + 14, report, notebook)
        y = self._draw_footer(inner, y + 22, report, notebook)

        inner_h = int(y + 10)
        inner.image = inner.image.crop((0, 0, CONTENT_W * SCALE, inner_h * SCALE))

        page_h = MARGIN * 2 + inner_h + CARD_PAD * 2
        page = Canvas(self._fonts, PAGE_W, page_h)
        page.rect((0, 0, PAGE_W, page_h), fill=(*PAPER, 255))
        for dy in range(12, page_h - 10, 22):
            for dx in range(12, PAGE_W - 10, 22):
                page.ellipse((dx - 1.5, dy - 1.5, dx + 1.5, dy + 1.5),
                             fill=(221, 221, 221, 255))
        card = (MARGIN, MARGIN, PAGE_W - MARGIN, page_h - MARGIN)
        page.rr((card[0] + 16, card[1] + 16, card[2] + 16, card[3] + 16), 10, fill=(*PINK, 255))
        page.rr((card[0] + 8, card[1] + 8, card[2] + 8, card[3] + 8), 10, fill=(*BLUE, 255))
        page.rr(card, 10, fill=(*CARD_BG, 255))
        page.dashed_rr((card[0] + 12, card[1] + 12, card[2] - 12, card[3] - 12),
                       6, (215, 204, 200, 255), 2)
        page.paste_canvas(inner, (MARGIN + CARD_PAD, MARGIN + CARD_PAD))

        final = page.image.resize((PAGE_W, page_h), Image.LANCZOS).convert("RGB")
        png_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = png_path.with_name(f".{png_path.name}.tmp")
        final.save(tmp, "PNG")
        tmp.replace(png_path)
        return (PAGE_W, page_h)

    # ------------------------------------------------------------ 页眉

    def _draw_header(self, c: Canvas, y: float, report: NotebookReport,
                     notebook: Any) -> float:
        brand = " ".join(list((notebook.brand_text or "GROUP NOTEBOOK").replace(" ", "")))
        c.text((0, y), brand, "bold", 11, INK3)
        room = report.room_name
        c.text((CONTENT_W - c.tw(room, "body", 11), y), room, "body", 11, INK3)
        y += 26

        # 标题贴纸（微旋转）
        tlines = c.wrap(report.page_title, "title", 34, 400)
        sticker_w = min(430, max(max(c.tw(line, "title", 34) for line in tlines) + 56, 300))
        sticker_h = 26 + len(tlines) * 44 + 16
        layer = Canvas(self._fonts, sticker_w + 30, sticker_h + 30)
        layer.rr((21, 21, 21 + sticker_w, 21 + sticker_h), 8, fill=(*BLUE, 255))
        layer.rr((15, 15, 15 + sticker_w, 15 + sticker_h), 8,
                 fill=(*TITLE_BG, 255), outline=(*TITLE_BORDER, 255), width=3)
        ty = 15 + 14
        for line in tlines:
            layer.text((41, ty), line, "title", 34, TITLE_TEXT)
            ty += 44
        rotated = layer.rotated(1.5)
        c.paste_image(rotated, (-8, y - 10))
        y += sticker_h + 4

        # 印章
        if notebook.stamp_text:
            stamp = strip_emoji(notebook.stamp_text)
            sw = c.tw(stamp, "hand", 13) + 26
            c.rr((0, y, sw, y + 30), 6, fill=(255, 255, 255, 255),
                 outline=(*ORANGE, 255), width=2)
            c.text((13, y + 7), stamp, "hand", 13, ORANGE)
            y += 40

        # 问候（避让右侧立绘区域）
        for line in c.wrap(report.greeting, "hand", 15, 440):
            c.text((0, y), line, "hand", 15, INK2)
            y += 26
        y += 8

        # 日期便签（右侧）
        date_text = report.period_text or report.date
        if not report.period_text:
            try:
                date_obj = _dt.fromisoformat(report.date)
                date_text = (f"{date_obj.year} 年 {date_obj.month} 月 {date_obj.day} 日"
                             f" · {_WEEKDAYS[date_obj.weekday()]}")
            except ValueError:
                date_text = report.date
        dw = c.tw(date_text, "hand", 13) + 28
        layer = Canvas(self._fonts, dw + 20, 46)
        layer.rr((10, 8, 10 + dw, 40), 4, fill=(*YELLOW, 255),
                 outline=(230, 217, 128, 255), width=1)
        layer.text((24, 15), date_text, "hand", 13, (109, 76, 65))
        rotated = layer.rotated(2)
        c.paste_image(rotated, (CONTENT_W - dw - 2, y + 4))

        # 立绘（右上，画在便签下层）
        self._draw_character(c, report, notebook, y_top=40)
        return y + 16

    def _draw_character(self, c: Canvas, report: NotebookReport,
                        notebook: Any, y_top: float) -> None:
        meta = cover.load_meta()
        if meta and cover.image_path() is not None:
            path = cover.image_path()
            focus, height, mode = meta["focus"], int(meta["height"]), "cover"
        else:
            raw = str(getattr(notebook, "character_path", "") or "").strip()
            if not raw:
                return
            path = Path(raw)
            if not path.is_absolute():
                path = self._base / path
            if not path.is_file():
                return
            focus, height, mode = "center", 215, "contain"
        try:
            image = Image.open(path).convert("RGBA")
        except OSError:
            return
        if mode == "cover":
            target_w, target_h = 190, min(height, 340)
            src_ratio = image.width / image.height
            dst_ratio = target_w / target_h
            if src_ratio > dst_ratio:
                new_w = int(image.height * dst_ratio)
                left = (image.width - new_w) // 2
                box = (left, 0, left + new_w, image.height)
            else:
                new_h = int(image.width / dst_ratio)
                fy = 0.5 if focus == "center" else (0.0 if "top" in focus else 1.0)
                top = int((image.height - new_h) * fy)
                box = (0, top, image.width, top + new_h)
            cropped = image.crop(box).resize((target_w * SCALE, target_h * SCALE),
                                             Image.LANCZOS)
            mask = Image.new("L", cropped.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, cropped.width, cropped.height],
                                                   radius=12 * SCALE, fill=255)
            c.paste_masked(cropped, (CONTENT_W - target_w, y_top), mask)
        else:
            ratio = min(200 / image.width, 215 / image.height, 1.0)
            resized = image.resize((int(image.width * ratio * SCALE),
                                    int(image.height * ratio * SCALE)), Image.LANCZOS)
            c.paste_image(resized, (CONTENT_W - int(resized.width / SCALE), y_top))

    # ------------------------------------------------------------ 统计卡

    def _draw_stats(self, c: Canvas, y: float, report: NotebookReport) -> float:
        stats = report.stats
        cards = [
            (str(stats.message_count), "收录于摘录的消息"),
            (str(stats.member_count), "参与聊天的人数"),
            (str(stats.char_count), "留在纸页上的文字"),
            (str(stats.emoji_count), "贴在纸页上的表情"),
        ]
        gap = 12
        card_w = (CONTENT_W - gap * 3) // 4
        card_h = 84
        for index, (num, label) in enumerate(cards):
            cx = index * (card_w + gap)
            c.rr((cx, y, cx + card_w, y + card_h), 10,
                 fill=(255, 255, 255, 255), outline=(*INK, 255), width=2)
            c.rr((cx + 4, y + 4, cx + card_w - 4, y + card_h - 4), 7,
                 outline=(*INK_FAINT, 255), width=1)
            c.text((cx + (card_w - c.tw(num, "title", 24)) // 2, y + 10),
                   num, "title", 24, INK)
            c.text((cx + (card_w - c.tw(label, "body", 10)) // 2, y + 52),
                   label, "body", 10, INK2)
        return y + card_h

    # ------------------------------------------------------------ 小节头

    def _section_head(self, c: Canvas, y: float, tag: str, title: str, note: str) -> float:
        tag_w = c.tw(tag, "bold", 10) + 16
        c.rr((0, y, tag_w, y + 20), 4, fill=(*INK, 255))
        c.text((8, y + 3), tag, "bold", 10, TITLE_BG)
        c.text((tag_w + 10, y - 4), title, "title", 21, (78, 52, 46))
        if note:
            c.text((CONTENT_W - c.tw(note, "body", 11), y + 5), note, "body", 11, INK3)
        return y + 32

    # ------------------------------------------------------------ 24H 图

    def _draw_time(self, c: Canvas, y: float, report: NotebookReport,
                   notebook: Any) -> float:
        y = self._section_head(c, y, "TIME", notebook.time_section_title,
                               f"24 小时时间轴 · 最活跃 {report.stats.peak_hour:02d}:00")
        card_h = 236
        c.rr((0, y, CONTENT_W, y + card_h), 12,
             fill=(255, 255, 255, 255), outline=(*INK, 255), width=2)
        for hy in range(int(y) + 16, int(y + card_h) - 14, 16):
            c.ellipse((12, hy, 20, hy + 8), fill=(239, 235, 233, 255))
        chart_x, chart_right = 36, CONTENT_W - 16
        chart_w = chart_right - chart_x
        hourly = report.stats.hourly
        peak = max(range(24), key=lambda i: hourly[i])
        peak_count = max(1, max(hourly))
        base_y = y + card_h - 34
        bar_w = (chart_w - 23 * 4) / 24
        for hour in range(24):
            count = hourly[hour]
            bx = chart_x + hour * (bar_w + 4)
            bar_h = max(4, round(count / peak_count * 148))
            color = ORANGE if (hour == peak and count > 0) else (
                BLUE if hour % 2 == 0 else GREEN)
            c.rr((bx, base_y - bar_h, bx + bar_w, base_y), 4, fill=(*color, 255))
            if count > 0:
                c.text((bx + (bar_w - c.tw(str(count), "body", 9)) / 2,
                        base_y - bar_h - 15), str(count), "body", 9, INK2)
            label = f"{hour:02d}"
            c.text((bx + (bar_w - c.tw(label, "body", 8)) / 2, base_y + 8),
                   label, "body", 8, INK_FAINT)
        return y + card_h

    # ------------------------------------------------------------ 话题

    def _topic_block_height(self, topic: Topic, names: list[str], c: Canvas) -> int:
        detail = self._expand_mentions(strip_emoji(topic.detail), names)
        detail_lines = c.wrap(detail, "body", 13, CONTENT_W - 90)
        h = 30 + len(detail_lines) * 24 + 12
        if topic.contributors:
            h += 26
        return h

    def _expand_mentions(self, text: str, names: list[str]) -> str:
        out = ""
        for kind, seg in _split_mentions(text, names):
            out += ("@" * 6 if kind == "m" else seg)
        return out

    def _draw_topics(self, c: Canvas, y: float, report: NotebookReport,
                     notebook: Any, names: list[str]) -> float:
        topics = report.topics
        y = self._section_head(c, y, "TOPIC", notebook.topic_section_title,
                               f"本页摘抄了 {len(topics)} 个话题")
        if not topics:
            return y
        card_h = 24 + sum(self._topic_block_height(t, names, c) for t in topics) + 8
        c.rr((0, y, CONTENT_W, y + card_h), 12,
             fill=(255, 255, 255, 255), outline=(*INK, 255), width=2)
        for ly in range(int(y) + 28, int(y + card_h) - 8, 28):
            c.line([(40, ly), (CONTENT_W - 14, ly)], (236, 226, 207, 255), 1)
        for hy in range(int(y) + 18, int(y + card_h) - 16, 34):
            c.ellipse((16, hy, 27, hy + 11), fill=(239, 235, 233, 255),
                      outline=(210, 200, 190, 255), width=1)

        ty = y + 14
        for index, topic in enumerate(topics):
            c.rr((46, ty, 66, ty + 20), 4, fill=(255, 255, 255, 255),
                 outline=(*INK, 255), width=2)
            c.line([(51, ty + 10), (56, ty + 15), (62, ty + 5)], ORANGE, 3)
            tw = c.tw(topic.title, "title", 18)
            hl = _HL_COLORS[["pink", "blue", "green"][index % 3]]
            c.rect((74, ty + 8, 78 + tw, ty + 25), fill=(*hl, 255))
            c.text((78, ty), topic.title, "title", 18, (78, 52, 46))
            idx = f"#{index + 1:02d}"
            c.text((CONTENT_W - c.tw(idx, "body", 10) - 16, ty + 6), idx, "body", 10,
                   INK_FAINT)
            ty += 30
            if topic.contributors:
                cx = 74
                for name in topic.contributors:
                    color = color_for(name)
                    chip_w = 26 + c.tw(name, "body", 11)
                    if cx + chip_w > CONTENT_W - 40:
                        ty += 26
                        cx = 74
                    c.rr((cx, ty, cx + chip_w, ty + 22), 11,
                         fill=(255, 255, 255, 255), outline=(*color, 255), width=2)
                    c.ellipse((cx + 4, ty + 4, cx + 18, ty + 18), fill=(*color, 255))
                    initial = next((ch for ch in name if not ch.isspace()), "?")
                    c.text((cx + 11 - c.tw(initial, "body", 9) / 2, ty + 5),
                           initial, "body", 9, (255, 255, 255, 255))
                    c.text((cx + 23, ty + 3), name, "body", 11, INK)
                    cx += chip_w + 8
                c.text((cx + 6, ty + 5), "参与了这段", "body", 10, INK_FAINT)
                ty += 26
            # 详情（含 @胶囊）
            segs = [
                (kind, strip_emoji(seg))
                for kind, seg in _split_mentions(topic.detail, names)
            ]
            cx = 74
            font_unit = c.tw("字", "body", 13)
            for kind, seg in segs:
                if not seg:
                    continue
                if kind == "t":
                    for char in seg:
                        if (cx + font_unit) > CONTENT_W - 24:
                            ty += 24
                            cx = 74
                        c.text((cx, ty), char, "body", 13, INK)
                        cx += font_unit
                else:
                    pill_text = "@" + seg
                    pill_w = c.tw(pill_text, "body", 12) + 18
                    if cx + pill_w > CONTENT_W - 24:
                        ty += 24
                        cx = 74
                    c.rr((cx, ty - 2, cx + pill_w, ty + 20), 10,
                         fill=(255, 243, 224, 255), outline=(255, 204, 128, 255), width=1)
                    c.text((cx + 9, ty + 1), pill_text, "body", 12, (230, 81, 0))
                    cx += pill_w + 4
            ty += 24
            ty += 12
        return y + card_h

    # ------------------------------------------------------------ 今日总结

    def _draw_recap(self, c: Canvas, y: float, report: NotebookReport,
                    notebook: Any) -> float:
        if not (getattr(notebook, "recap_enabled", True) and report.recap):
            return y
        y = self._section_head(c, y, "RECAP", notebook.recap_section_title,
                               "把今天的纸页合成一段话")
        lines = c.wrap(strip_emoji(report.recap), "hand", 15, CONTENT_W - 52)
        card_h = 18 + len(lines) * 28 + 36
        c.rr((6, y + 6, CONTENT_W - 2, y + card_h + 6), 12, fill=(*PURPLE, 255))
        c.rr((0, y, CONTENT_W - 8, y + card_h), 12,
             fill=(255, 255, 255, 255), outline=(*INK, 255), width=2)
        ry = y + 14
        for line in lines:
            c.text((22, ry), line, "hand", 15, (78, 52, 46))
            ry += 28
        sign = f"—— {report.bot_name} 记"
        c.text((CONTENT_W - c.tw(sign, "hand", 12) - 22, y + card_h - 26),
               sign, "hand", 12, INK3)
        return y + card_h

    # ------------------------------------------------------------ 群友侧写

    def _draw_people(self, c: Canvas, y: float, report: NotebookReport,
                     notebook: Any) -> float:
        portraits = report.portraits
        y = self._section_head(c, y, "PEOPLE", notebook.people_section_title,
                               f"本页一共画了 {len(portraits)} 位群友")
        if not portraits:
            return y
        gap = 14
        col_w = (CONTENT_W - gap) // 2

        def card_height(p: Portrait) -> int:
            desc_lines = c.wrap(strip_emoji(p.desc), "body", 12, col_w - 28)[:4]
            return 62 + (26 if (p.mbti or p.tag) else 4) + len(desc_lines) * 20 + 22

        angles = (-1.2, 1.0, -0.6, 0.8, -1.4, 0.5)
        row = 0
        index = 0
        max_bottom = y
        while index < len(portraits):
            left = portraits[index]
            right = portraits[index + 1] if index + 1 < len(portraits) else None
            row_h = max(card_height(left), card_height(right) if right else 0)
            for col, p in enumerate((left, right)):
                if p is None:
                    continue
                x = col * (col_w + gap)
                ch = card_height(p)
                color = color_for(p.name)
                angle = angles[index % 6] if False else angles[(index + col) % 6]
                layer = Canvas(self._fonts, col_w + 24, ch + 24)
                ox, oy = 12, 12
                layer.rr((ox + 4, oy + 4, ox + col_w + 4, oy + ch + 4), 12,
                         fill=(93, 64, 55, 40))
                layer.rr((ox, oy, ox + col_w, oy + ch), 12,
                         fill=(255, 251, 240, 255), outline=(*INK, 255), width=2)
                # 头像
                layer.ellipse((ox + 14, oy + 14, ox + 52, oy + 52),
                              fill=(*color, 255), outline=(*INK, 255), width=2)
                initial = next((ch for ch in p.name if not ch.isspace()), "?")
                layer.text((ox + 33 - layer.tw(initial, "title", 17) / 2, oy + 22),
                           initial, "title", 17, (255, 255, 255, 255))
                layer.text((ox + 60, oy + 15), p.name, "title", 16, (78, 52, 46))
                layer.text((ox + 60, oy + 37), p.title, "body", 11, INK2)
                by = oy + 62
                bx = ox + 60
                if p.mbti:
                    w = layer.tw(p.mbti, "bold", 10) + 16
                    layer.rr((bx, by, bx + w, by + 20), 6,
                             fill=(*MBTI_BG, 255), outline=(*MBTI_BORDER, 255), width=1)
                    layer.text((bx + 8, by + 3), p.mbti, "bold", 10, MBTI_TEXT)
                    bx += w + 8
                if p.tag:
                    w = layer.tw(p.tag, "body", 10) + 16
                    layer.rr((bx, by, bx + w, by + 20), 6,
                             fill=(245, 245, 245, 255), outline=(189, 189, 189, 255),
                             width=1)
                    layer.text((bx + 8, by + 3), p.tag, "body", 10, (97, 97, 97, 255))
                layer.rr((ox + 12, oy + 90, ox + col_w - 12, oy + ch - 12), 8,
                         fill=(248, 251, 255, 255))
                dy = oy + 96
                for line in c.wrap(strip_emoji(p.desc), "body", 12, col_w - 28)[:4]:
                    layer.text((ox + 22, dy), line, "body", 12, INK)
                    dy += 20
                # 胶带
                layer.rr((ox + col_w / 2 - 42, oy - 8, ox + col_w / 2 + 42, oy + 8), 2,
                         fill=(255, 213, 79, 140) if col == 0 else
                              ((129, 199, 192, 128) if index % 3 else
                               (149, 117, 205, 102)))
                rotated = layer.rotated(angle)
                c.paste_image(rotated, (x - 12, y + row * (row_h + 16) - 12))
                max_bottom = max(max_bottom,
                                 y + row * (row_h + 16) + ch)
            index += 2
            row += 1
        return max_bottom + 6

    # ------------------------------------------------------------ 金句

    def _draw_quotes(self, c: Canvas, y: float, report: NotebookReport,
                     notebook: Any) -> float:
        quotes = report.quotes
        y = self._section_head(c, y, "QUOTE", notebook.quote_section_title,
                               f"从今天的聊天里挑了 {len(quotes)} 句")
        for index, quote in enumerate(quotes):
            alt = index % 2 == 1
            color = color_for(quote.sender)
            c.ellipse((0, y, 26, y + 26), fill=(*color, 255), outline=(*INK, 255), width=2)
            initial = next((ch for ch in quote.sender if not ch.isspace()), "?")
            c.text((13 - c.tw(initial, "body", 12) / 2, y + 6), initial,
                   "body", 12, (255, 255, 255, 255))
            name_w = c.tw(quote.sender, "hand", 14)
            c.rect((34, y + 13, 38 + name_w, y + 22), fill=(255, 245, 157, 255))
            c.text((36, y + 3), quote.sender, "hand", 14, (78, 52, 46))
            y += 34
            body = strip_emoji(quote.text)
            text_lines = c.wrap(body, "hand", 16, CONTENT_W - 52)
            bubble_h = 18 + len(text_lines) * 28 + 10
            shadow = PINK if not alt else BLUE
            bubble_fill = (255, 255, 255, 255) if not alt else (*TITLE_BG, 255)
            c.rr((8, y + 5, CONTENT_W - 2, y + bubble_h + 5), 12, fill=(*shadow, 255))
            c.rr((4, y, CONTENT_W - 6, y + bubble_h), 12,
                 fill=bubble_fill, outline=(93, 64, 55, 60), width=1)
            qy = y + 10
            for li, line in enumerate(text_lines):
                shown = ("“" + line + "”") if li == len(text_lines) - 1 else line
                c.text((22, qy), shown, "hand", 16, (78, 52, 46))
                qy += 28
            y += bubble_h + 10
            comment_lines = c.wrap(strip_emoji(quote.comment), "body", 12, CONTENT_W - 84)
            note_h = 20 + len(comment_lines) * 20 + 10
            note_bg = TITLE_BG if not alt else (243, 229, 245)
            accent = ORANGE if not alt else PURPLE_DEEP
            label_color = TITLE_TEXT if not alt else (106, 27, 154)
            c.rr((34, y, CONTENT_W - 16, y + note_h), 6, fill=(*note_bg, 255))
            c.line([(36, y + 3), (36, y + note_h - 3)], accent, 4)
            c.text((48, y + 5), f"{report.bot_name}锐评", "bold", 10, label_color)
            cy = y + 24
            for line in comment_lines:
                c.text((48, cy), line, "body", 12, (109, 76, 65))
                cy += 20
            y += note_h + 12
        return y

    # ------------------------------------------------------------ 聊天天气

    def _draw_mood(self, c: Canvas, y: float, report: NotebookReport,
                   notebook: Any) -> float:
        mood = report.mood
        y = self._section_head(c, y, "MOOD", notebook.mood_section_title,
                               mood.subtitle or "按消息占比分成的几种天气")
        dims = list(mood.dimensions)[:6]
        sticker_rows = (len(dims) + 1) // 2
        summary_lines = c.wrap(strip_emoji(mood.summary), "hand", 14,
                               CONTENT_W - 160)
        summary_h = max(60, 14 + len(summary_lines) * 26)
        card_h = 74 + (sticker_rows * 116 if dims else 8) + summary_h + 18
        c.rr((0, y, CONTENT_W, y + card_h), 12,
             fill=(255, 251, 230, 255), outline=(*INK, 255), width=2)
        title = mood.title or "今日聊天天气"
        badge_w = c.tw(title, "title", 17) + 32
        c.rr((16, y + 14, 16 + badge_w, y + 46), 8, fill=(*ORANGE, 255))
        c.text((32, y + 19), title, "title", 17, (255, 255, 255))
        ty = y + 56
        if dims:
            bar_x, bar_w2, bar_h = 18, CONTENT_W - 36, 24
            total = sum(max(0, d.percentage) for d in dims) or 1
            bx = bar_x
            bar_layer = Canvas(self._fonts, int(bar_w2), bar_h)
            for index, dim in enumerate(dims):
                seg_w = int(bar_w2 * max(0, dim.percentage) / total)
                if seg_w <= 0:
                    continue
                bar_layer.rect((bx, 0, bx + seg_w, bar_h),
                               fill=(*PALETTE_RGB[index % len(PALETTE_RGB)], 255))
                bx += seg_w
            mask = Image.new("L", bar_layer.image.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                [0, 0, bar_layer.image.width - 1, bar_layer.image.height - 1],
                radius=10 * SCALE, fill=255)
            c.paste_masked(bar_layer.image, (bar_x, ty), mask)
            c.rr((bar_x, ty, bar_x + bar_w2, ty + bar_h), 10,
                 outline=(*INK, 255), width=2)
            ty += 18
            sticker_w = (CONTENT_W - 36 - 14) // 2
            for index, dim in enumerate(dims):
                col = index % 2
                row = index // 2
                sx = 18 + col * (sticker_w + 14)
                sy = ty + row * 116
                color = PALETTE_RGB[index % len(PALETTE_RGB)]
                c.rr((sx + 3, sy + 3, sx + sticker_w + 3, sy + 106 + 3), 10,
                     fill=(230, 220, 210, 120))
                c.rr((sx, sy, sx + sticker_w, sy + 106), 10,
                     fill=(255, 255, 255, 255), outline=(*color, 255), width=2)
                c.text((sx + 12, sy + 10), dim.name, "title", 14, (78, 52, 46))
                pct = f"{max(0, dim.percentage)}%"
                c.text((sx + sticker_w - c.tw(pct, "bold", 13) - 12, sy + 12),
                       pct, "bold", 13, color)
                cy = sy + 40
                for line in c.wrap(strip_emoji(dim.comment), "body", 11,
                                   sticker_w - 24)[:3]:
                    c.text((sx + 12, cy), line, "body", 11, (109, 76, 65))
                    cy += 18
            ty += sticker_rows * 116
        # 总结气泡 + 立绘
        bubble_x = 20
        try:
            from .renderer import character_info  # noqa: PLC0415

            info = character_info(self._config, self._base)
            if info.get("present") and info.get("uri"):
                image = Image.open(info["uri"].replace("file:///", "", 1)).convert("RGBA")
                ratio = min(100 / image.width, 100 / image.height, 1.0)
                image = image.resize((max(1, int(image.width * ratio * SCALE)),
                                      max(1, int(image.height * ratio * SCALE))),
                                     Image.LANCZOS)
                c.paste_image(image, (20, y + card_h - 20 - int(image.height / SCALE)))
                bubble_x = 20 + int(image.width / SCALE) + 12
        except (OSError, ValueError):
            bubble_x = 20
        bubble_y = y + card_h - 18 - max(60, 14 + len(summary_lines) * 26)
        c.rr((bubble_x, bubble_y, CONTENT_W - 8, y + card_h - 12), 14,
             fill=(255, 255, 255, 255), outline=(*INK, 255), width=2)
        my = bubble_y + 10
        for line in summary_lines:
            c.text((bubble_x + 14, my), line, "hand", 14, (78, 52, 46))
            my += 26
        return y + card_h

    # ------------------------------------------------------------ 咖啡/碎碎念/页脚

    def _draw_coffee(self, c: Canvas, y: float, report: NotebookReport,
                     notebook: Any) -> float:
        if not (getattr(notebook, "coffee_enabled", True) and report.coffee):
            return y
        title = strip_emoji(f"{report.bot_name}的咖啡推荐")
        lines = c.wrap(strip_emoji(report.coffee), "hand", 14, CONTENT_W - 52)
        card_h = 40 + len(lines) * 26 + 12
        c.rr((0, y, CONTENT_W, y + card_h), 12,
             fill=(246, 236, 221, 255), outline=(161, 136, 127, 255), width=2)
        c.dashed_rr((6, y + 6, CONTENT_W - 6, y + card_h - 6), 9,
                    (161, 136, 127, 255), 1)
        c.text((22, y + 10), title, "title", 16, (109, 76, 65))
        cy = y + 42
        for line in lines:
            c.text((22, cy), line, "hand", 14, (93, 64, 55))
            cy += 26
        return y + card_h

    def _draw_murmur(self, c: Canvas, y: float, report: NotebookReport,
                     notebook: Any) -> float:
        if not (getattr(notebook, "murmur_enabled", True) and report.murmur):
            return y
        title = strip_emoji(f"{report.bot_name}的碎碎念")
        lines = c.wrap(strip_emoji(report.murmur), "hand", 14, CONTENT_W - 52)
        card_h = 40 + len(lines) * 26 + 24
        c.rr((0, y, CONTENT_W, y + card_h), 12,
             fill=(237, 243, 250, 255), outline=(144, 164, 174, 255), width=2)
        c.dashed_rr((6, y + 6, CONTENT_W - 6, y + card_h - 6), 9,
                    (144, 164, 174, 255), 1)
        c.text((22, y + 10), title, "title", 16, (84, 110, 122))
        cy = y + 42
        for line in lines:
            c.text((22, cy), line, "hand", 14, (78, 90, 99))
            cy += 26
        sign = f"—— {report.bot_name}"
        c.text((CONTENT_W - c.tw(sign, "hand", 12) - 22, y + card_h - 24),
               sign, "hand", 12, (144, 164, 174))
        return y + card_h

    def _draw_footer(self, c: Canvas, y: float, report: NotebookReport,
                     notebook: Any) -> float:
        from .renderer import plugin_version  # noqa: PLC0415

        card_h = 78
        c.dashed_rr((0, y, CONTENT_W, y + card_h), 10, (*INK_FAINT, 255), 2)
        gap = 12
        col_w = (CONTENT_W - 24 - gap * 2) // 3
        cols = [
            (report.page_title, report.period_text or report.date),
            (f"group_notebook {plugin_version(self._base)}", f"来源 {report.source}"),
            (notebook.brand_text, "手账 · 摘录 · 聊天天气"),
        ]
        for index, (strong, sub) in enumerate(cols):
            cx = 12 + index * (col_w + gap)
            c.rr((cx, y + 10, cx + col_w, y + card_h - 10), 6,
                 fill=(255, 255, 255, 255))
            c.text((cx + 10, y + 18), strip_emoji(strong)[:22], "title", 12, INK)
            c.text((cx + 10, y + 44), strip_emoji(sub)[:24], "body", 10, INK2)
        return y + card_h
