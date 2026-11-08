"""专题案底（卷宗风）的 Pillow 渲染器，与手账引擎共用 Canvas 体系。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .models import DossierReport
from .pillow_renderer import (
    BLUE,
    INK,
    INK2,
    INK3,
    INK_FAINT,
    PAPER,
    SCALE,
    _Fonts,
    _hex_rgb,
)
from .renderer import character_info, color_for, initial_for, plugin_version

SEAL = (179, 56, 44)
SEAL_SOFT = (232, 200, 189)
KRAFT = (243, 231, 205)
FOLDER = (251, 245, 232)
TAPE = (196, 160, 100, 110)

PAGE_W = 750
_MARGIN = 22
_PAD = 24
_CONTENT_W = PAGE_W - _MARGIN * 2 - _PAD * 2  # 658


class PillowDossierRenderer:
    """用 Pillow 绘制卷宗风案底长图。"""

    def __init__(self, config: Any, base_dir: Path) -> None:
        self._config = config
        self._base = Path(base_dir).resolve()
        self._fonts = _Fonts(self._base)

    # ------------------------------------------------------------ 入口

    def render(self, report: DossierReport, png_path: Path) -> tuple[int, int]:
        """渲染案底并保存 PNG，返回 (宽, 高)。"""
        inner = Image.new("RGBA", (_CONTENT_W * SCALE, 9000 * SCALE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(inner)
        y = 0.0
        y = self._draw_head(draw, inner, y, report)
        y = self._draw_intro(draw, y + 18, report)
        y = self._draw_culprits(draw, y + 24, report)
        y = self._draw_cases(draw, y + 24, report)
        y = self._draw_closing(draw, y + 22, report)
        y = self._draw_footer(draw, y + 20, report)

        inner_h = int(y + 8)
        inner = inner.crop((0, 0, _CONTENT_W * SCALE, inner_h * SCALE))

        page_h = _MARGIN * 2 + inner_h + _PAD * 2
        page = Image.new("RGBA", (PAGE_W * SCALE, page_h * SCALE), (0, 0, 0, 0))
        pd = ImageDraw.Draw(page)
        pd.rectangle([0, 0, page.width, page.height], fill=(*KRAFT, 255))
        for dy in range(16, page_h - 12, 14):
            for dx in range(16, PAGE_W - 12, 26):
                pd.line([(dx, dy), (dx + 9, dy + 7)], fill=(226, 212, 180, 255), width=1)
        card = (_MARGIN, _MARGIN, PAGE_W - _MARGIN, page_h - _MARGIN)
        pd.rounded_rectangle([card[0] + 10 * SCALE, card[1] + 12 * SCALE,
                              card[2] + 8 * SCALE, card[3] + 10 * SCALE],
                             radius=8 * SCALE, fill=(74, 55, 40, 90))
        pd.rounded_rectangle(card, radius=8 * SCALE, fill=(*FOLDER, 255),
                             outline=(*INK, 255), width=2 * SCALE)
        page.alpha_composite(inner, (int((_MARGIN + _PAD) * SCALE), int((_MARGIN + _PAD) * SCALE)))

        final = page.resize((PAGE_W, page_h), Image.LANCZOS).convert("RGB")
        png_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = png_path.with_name(f".{png_path.name}.tmp")
        final.save(tmp, "PNG")
        tmp.replace(png_path)
        return (PAGE_W, page_h)

    # ------------------------------------------------------------ 页眉

    def _draw_head(self, draw: ImageDraw.Draw, page: Image.Image,
                   y: float, report: DossierReport) -> float:
        x = 26
        c_no = report.case_no
        self._chip(draw, x, y, c_no)
        room_w = self._tw(draw, report.room_name, "body", 12)
        self._text(draw, (_CONTENT_W - 26 - room_w, y), report.room_name, "body", 12, INK3)
        y += 34
        title_lines = self._wrap(draw, report.page_title, "title", 38, 470)
        ty = y
        for line in title_lines:
            self._text(draw, (x, ty), line, "title", 38, SEAL)
            ty += 48
        y = max(ty, y + 60) + 8
        direction_text = ("成员迫害 bot" if report.direction == "member_as_culprit"
                          else "成员被迫害")
        victim_show = report.victim_display or report.bot_name
        focus = (f"方向：{direction_text}　·　专题焦点：{report.focus_word}"
                 f"　·　受害者：{victim_show}")
        self._text(draw, (x, y), focus, "hand", 15, INK2)
        y += 28
        # 印章（右）
        ring = 104
        cx = _CONTENT_W - 26 - ring // 2
        cy = y - ring // 2 - 6
        draw.ellipse([(cx - ring / 2) * SCALE, (cy - ring / 2) * SCALE,
                      (cx + ring / 2) * SCALE, (cy + ring / 2) * SCALE],
                     outline=(*SEAL, 200), width=4 * SCALE)
        for line in ("已立案", "示众"):
            lw = self._tw(draw, line, "title", 20)
            self._text(draw, (cx - lw / 2, cy - 24), line, "title", 20, SEAL)
        sub = report.period_text
        lw = self._tw(draw, sub, "body", 9)
        self._text(draw, (cx - lw / 2, cy + 22), sub, "body", 9, SEAL)
        # 受害者头像（印章左）
        self._draw_victim(draw, page, cx - ring / 2 - 60, cy - 40, report)
        return y + 20

    def _draw_victim(self, draw: ImageDraw.Draw, page: Image.Image,
                     x: float, y: float, report: DossierReport) -> None:
        from .renderer import character_info  # noqa: PLC0415

        info = character_info(self._config, self._base)
        if not (info.get("present") and info.get("uri")):
            return
        try:
            image = Image.open(info["uri"].replace("file:///", "", 1)).convert("RGBA")
        except OSError:
            return
        ratio = min(90 / image.width, 90 / image.height, 1.0)
        image = image.resize((max(1, int(image.width * ratio * SCALE)),
                              max(1, int(image.height * ratio * SCALE))), Image.LANCZOS)
        page.alpha_composite(image, (int(x * SCALE), int(y * SCALE)))

    def _chip(self, draw: ImageDraw.Draw, x: float, y: float, text: str) -> None:
        w = self._tw(draw, text, "bold", 12) + 22
        draw.rounded_rectangle([x * SCALE, y * SCALE, (x + w) * SCALE, (y + 24) * SCALE],
                               radius=4 * SCALE, fill=(255, 255, 255, 200),
                               outline=(*INK3, 255), width=SCALE)
        self._text(draw, (x + 11, y + 4), text, "bold", 12, INK2)

    # ------------------------------------------------------------ 受害者陈述

    def _draw_intro(self, draw: ImageDraw.Draw, y: float, report: DossierReport) -> float:
        from .renderer import plain_html  # noqa: PLC0415
        import html as html_module

        label = "受害者陈述"
        self._text(draw, (26, y), label, "bold", 11, SEAL)
        y += 22
        lines = self._wrap(draw, strip_danger(report.intro), "hand", 15, _CONTENT_W - 70)
        card_h = 18 + len(lines) * 28 + 12
        draw.rounded_rectangle([26 * SCALE, y * SCALE, (_CONTENT_W - 8) * SCALE,
                                (y + card_h) * SCALE], radius=8 * SCALE,
                               fill=(255, 255, 255, 255))
        draw.rectangle([26 * SCALE, y * SCALE, (26 + 6) * SCALE, (y + card_h) * SCALE],
                       fill=(*SEAL, 255))
        ly = y + 12
        for line in lines:
            self._text(draw, (52, ly), line, "hand", 15, INK)
            ly += 28
        return y + card_h

    # ------------------------------------------------------------ 主犯通缉栏

    def _draw_culprits(self, draw: ImageDraw.Draw, y: float,
                       report: DossierReport) -> float:
        y = self._section_head(draw, y, "WANTED", "主犯通缉栏",
                               "按与受害者互动次数排序")
        culprits = report.culprits
        if not culprits:
            return y
        gap = 12
        col_w = (_CONTENT_W - 26 - gap * 2) // 3
        for index, p in enumerate(culprits):
            x = 26 + index * (col_w + gap)
            color = _hex_rgb(color_for(p.name))
            card_h = 118
            draw.rounded_rectangle([(x + 4) * SCALE, (y + 4) * SCALE,
                                    (x + col_w + 4) * SCALE, (y + card_h + 4) * SCALE],
                                   radius=10 * SCALE, fill=(74, 55, 40, 70))
            draw.rounded_rectangle([x * SCALE, y * SCALE, (x + col_w) * SCALE,
                                    (y + card_h) * SCALE], radius=10 * SCALE,
                                   fill=(255, 255, 255, 255), outline=(*INK, 255),
                                   width=2 * SCALE)
            # 胶带
            draw.rounded_rectangle([(x + col_w / 2 - 34) * SCALE, (y - 7) * SCALE,
                                    (x + col_w / 2 + 34) * SCALE, (y + 7) * SCALE],
                                   radius=2 * SCALE, fill=(196, 160, 100, 110))
            # 头像
            draw.ellipse([(x + 12) * SCALE, (y + 12) * SCALE, (x + 50) * SCALE,
                          (y + 50) * SCALE], fill=(*color, 255),
                         outline=(*INK, 255), width=2 * SCALE)
            initial = initial_for(p.name)
            iw = self._tw(draw, initial, "title", 17)
            self._text(draw, (x + 31 - iw / 2, y + 22), initial, "title", 17,
                       (255, 255, 255, 255))
            self._text(draw, (x + 58, y + 13), p.name, "title", 15, INK)
            # 称号章
            title = p.title or "惯犯"
            tw = self._tw(draw, title, "bold", 10) + 14
            draw.rounded_rectangle([(x + 58) * SCALE, (y + 36) * SCALE,
                                    (x + 58 + tw) * SCALE, (y + 54) * SCALE],
                                   radius=4 * SCALE, fill=(*SEAL, 255))
            self._text(draw, (x + 65, y + 39), title, "bold", 10, (255, 255, 255, 255))
            # 次数
            count_text = str(p.count)
            cw = self._tw(draw, count_text, "title", 20)
            self._text(draw, (x + col_w - cw - 12, y + 14), count_text, "title", 20, SEAL)
            self._text(draw, (x + col_w - 44, y + 42), "次互动", "body", 9, INK3)
            # 画像
            profile_lines = self._wrap(draw, strip_danger(p.profile), "body", 11, col_w - 24)[:2]
            py = y + 66
            for line in profile_lines:
                self._text(draw, (x + 12, py), line, "body", 11, INK)
                py += 18
        return y + 126

    # ------------------------------------------------------------ 案件

    def _draw_cases(self, draw: ImageDraw.Draw, y: float,
                    report: DossierReport) -> float:
        y = self._section_head(draw, y, "CASES", "案件卷宗",
                               f"共 {len(report.cases)} 起，按恶劣程度排序")
        for index, case in enumerate(report.cases):
            color = _hex_rgb(color_for(case.culprits[0] if case.culprits else "?"))
            summary_lines = self._wrap(draw, strip_danger(case.summary), "body", 13,
                                       _CONTENT_W - 76)[:4]
            comment_lines = self._wrap(draw, strip_danger(case.comment), "body", 12,
                                       _CONTENT_W - 96)[:3]
            card_h = 60 + len(summary_lines) * 24 + 14 + len(comment_lines) * 20 + 16
            draw.rounded_rectangle([(26 + 6) * SCALE, (y + 6) * SCALE,
                                    (_CONTENT_W - 2 + 6) * SCALE, (y + card_h + 6) * SCALE],
                                   radius=10 * SCALE, fill=(74, 55, 40, 55))
            draw.rounded_rectangle([26 * SCALE, y * SCALE, (_CONTENT_W - 2) * SCALE,
                                    (y + card_h) * SCALE], radius=10 * SCALE,
                                   fill=(255, 255, 255, 255), outline=(*INK, 255),
                                   width=2 * SCALE)
            # 回形针
            draw.rounded_rectangle([(26 + 30) * SCALE, (y - 9) * SCALE,
                                    (26 + 84) * SCALE, (y + 9) * SCALE],
                                   radius=8 * SCALE, outline=(*INK2, 255), width=2 * SCALE)
            # 头行
            no_text = f"案 {case.no:02d}"
            draw.rounded_rectangle([(26 + 16) * SCALE, (y + 12) * SCALE,
                                    (26 + 16 + self._tw(draw, no_text, "bold", 11) + 18) * SCALE,
                                    (y + 34) * SCALE], radius=4 * SCALE, fill=(*INK, 255))
            self._text(draw, (26 + 24, y + 15), no_text, "bold", 11,
                       (255, 255, 255, 255))
            self._text(draw, (26 + 110, y + 15), case.time, "hand", 13, INK2)
            stars = "★" * max(1, min(5, case.level)) + "☆" * (5 - max(1, min(5, case.level)))
            sw = self._tw(draw, stars, "body", 14)
            self._text(draw, (_CONTENT_W - 16 - sw, y + 14), stars, "body", 14, SEAL)
            # 主犯胶囊
            cx = 26 + 16
            cy = y + 46
            for name in case.culprits[:4]:
                chip_w = self._tw(draw, name, "body", 11) + 26
                draw.rounded_rectangle([cx * SCALE, cy * SCALE, (cx + chip_w) * SCALE,
                                        (cy + 22) * SCALE], radius=11 * SCALE,
                                       fill=(255, 255, 255, 255),
                                       outline=(*color, 255), width=SCALE)
                draw.ellipse([(cx + 4) * SCALE, (cy + 4) * SCALE, (cx + 18) * SCALE,
                              (cy + 18) * SCALE], fill=(*color, 255))
                self._text(draw, (cx + 22, cy + 3), name, "body", 11, INK)
                cx += chip_w + 8
            label = f"对 {case.victim} 实施了{report.focus_word}"
            self._text(draw, (cx + 6, cy + 4), label, "body", 10, INK3)
            # 案情
            sy = cy + 30
            for line in summary_lines:
                self._text(draw, (26 + 16, sy), line, "body", 13, INK)
                sy += 24
            # 批注
            ny = sy + 8
            note_h = 12 + len(comment_lines) * 20 + 8
            draw.rounded_rectangle([(26 + 16) * SCALE, ny * SCALE,
                                    (_CONTENT_W - 20) * SCALE, (ny + note_h) * SCALE],
                                   radius=6 * SCALE, fill=(*SEAL_SOFT, 255))
            draw.rectangle([(26 + 16) * SCALE, ny * SCALE, (26 + 20) * SCALE,
                            (ny + note_h) * SCALE], fill=(*SEAL, 255))
            self._text(draw, (26 + 32, ny + 5), f"{report.bot_name}批注", "bold", 10, SEAL)
            ny2 = ny + 24
            for line in comment_lines:
                self._text(draw, (26 + 32, ny2), line, "body", 12, (109, 76, 65))
                ny2 += 20
            y += card_h + 16
        return y

    # ------------------------------------------------------------ 结案/页脚

    def _draw_closing(self, draw: ImageDraw.Draw, y: float,
                      report: DossierReport) -> float:
        self._text(draw, (26, y), "结案陈词", "bold", 11, SEAL)
        y += 22
        lines = self._wrap(draw, strip_danger(report.closing), "hand", 15, _CONTENT_W - 120)
        card_h = max(64, 16 + len(lines) * 28 + 12)
        draw.rounded_rectangle([26 * SCALE, y * SCALE, (_CONTENT_W - 8) * SCALE,
                                (y + card_h) * SCALE], radius=12 * SCALE,
                               fill=(255, 255, 255, 230), outline=(*INK2, 255),
                               width=2 * SCALE)
        cy = y + 14
        for line in lines:
            self._text(draw, (44, cy), line, "hand", 15, INK)
            cy += 28
        return y + card_h

    def _draw_footer(self, draw: ImageDraw.Draw, y: float,
                     report: DossierReport) -> float:
        from .renderer import plugin_version  # noqa: PLC0415

        left = report.page_title
        mid = f"Neo-MoFox · 群聊摘录 {plugin_version(self._base)} · 来源 {report.source}"
        right = f"生成于 {report.generated_at}"
        self._text(draw, (26, y), left[:24], "body", 10, INK3)
        mw = self._tw(draw, mid, "body", 10)
        self._text(draw, ((_CONTENT_W - mw) / 2, y), mid, "body", 10, INK3)
        rw = self._tw(draw, right, "body", 10)
        self._text(draw, (_CONTENT_W - 26 - rw, y), right, "body", 10, INK3)
        return y + 22

    # ------------------------------------------------------------ 工具

    def _text(self, draw: ImageDraw.Draw, xy, text: str, kind: str, size: int, fill) -> int:
        font = self._fonts.get(kind, size * SCALE)
        draw.text((xy[0] * SCALE, xy[1] * SCALE), text, font=font, fill=fill)
        return int(font.getlength(text) / SCALE)

    def _tw(self, draw: ImageDraw.Draw, text: str, kind: str, size: int) -> int:
        return int(self._fonts.get(kind, size * SCALE).getlength(text) / SCALE)

    def _wrap(self, draw: ImageDraw.Draw, text: str, kind: str, size: int,
              max_w: int) -> list[str]:
        font = self._fonts.get(kind, size * SCALE)
        unit = max(1, int(font.getlength("字") / SCALE))
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

    def _section_head(self, draw: ImageDraw.Draw, y: float, tag: str,
                      title: str, note: str) -> float:
        tag_w = self._tw(draw, tag, "bold", 10) + 16
        draw.rounded_rectangle([26 * SCALE, y * SCALE, (26 + tag_w) * SCALE,
                                (y + 20) * SCALE], radius=4 * SCALE, fill=(*SEAL, 255))
        self._text(draw, (26 + 8, y + 3), tag, "bold", 10, (255, 255, 255, 255))
        self._text(draw, (26 + tag_w + 10, y - 4), title, "title", 21, INK)
        note_w = self._tw(draw, note, "body", 11)
        self._text(draw, (_CONTENT_W - 26 - note_w, y + 5), note, "body", 11, INK3)
        return y + 32


def strip_danger(text: str) -> str:
    """案底文本清洗：占位符折叠 + emoji 剔除（Pillow 无彩色字形）。"""
    from .pillow_renderer import strip_emoji  # noqa: PLC0415

    return strip_emoji(text)
