"""摘录产物的路径规则与历史列表。

只依赖标准库，WebUI 的 Router、Service 与测试共用同一套路径解析，
避免「生成时写在 A 目录、列表时扫 B 目录」这类分叉。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path

_SLUG_PATTERN = re.compile(r"[^0-9A-Za-z_-]+")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DEFAULT_BASE = "/plugins/group-notebook"


def slug_for_stream(stream_id: str) -> str:
    """把聊天流 ID 转成可安全用作目录名的标识。"""
    cleaned = _SLUG_PATTERN.sub("_", str(stream_id or "")).strip("_")
    return (cleaned or "stream")[:64]


def is_valid_day(value: str) -> bool:
    """判断字符串是否是合法的 ``YYYY-MM-DD`` 日期。"""
    text = str(value or "")
    if not _DATE_PATTERN.match(text):
        return False
    try:
        date_cls.fromisoformat(text)
    except ValueError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class ReportEntry:
    """一份已生成摘录的索引信息。kind: day=日报 / week=周报。"""

    slug: str
    day: str
    size: int
    mtime: float
    kind: str = "day"

    @property
    def mtime_text(self) -> str:
        """生成时间的可读文本。"""
        return datetime.fromtimestamp(self.mtime).strftime("%Y-%m-%d %H:%M")

    def to_dict(self) -> dict[str, object]:
        """转换为 JSON 友好的字典。"""
        return {
            "slug": self.slug,
            "date": self.day,
            "kind": self.kind,
            "size": self.size,
            "mtime": self.mtime,
            "mtime_text": self.mtime_text,
            "url": report_url(self.slug, self.day),
        }


def report_url(slug: str, day: str, base: str = _DEFAULT_BASE,
               kind: str = "day") -> str:
    """返回独立页面里可访问的海报地址。"""
    return (f"{base.rstrip('/')}/api/image?slug={slug}&date={day}"
            + (f"&kind={kind}" if kind != "day" else ""))


def parse_report_stem(stem: str) -> tuple[str, str] | None:
    """解析产物文件名：``YYYY-MM-DD`` → day，``week-YYYY-MM-DD`` → week。

    非法命名返回 None。
    """
    if stem.startswith("week-"):
        day = stem[5:]
        return ("week", day) if is_valid_day(day) else None
    return ("day", stem) if is_valid_day(stem) else None


def report_root(data_dir: Path) -> Path:
    """返回摘录根目录。"""
    return Path(data_dir) / "reports"


def report_file(data_dir: Path, slug: str, day: str,
                kind: str = "day") -> Path | None:
    """解析一份摘录的 PNG 路径，路径不合法或不存在时返回 None。

    路径穿越防护：slug 先重新清洗并比对，再校验 resolve 后仍位于
    产物根目录之内。
    """
    safe_slug = slug_for_stream(slug)
    if safe_slug != slug or not is_valid_day(day) or kind not in ("day", "week"):
        return None
    prefix = "week-" if kind == "week" else ""
    path = report_root(data_dir) / safe_slug / f"{prefix}{day}.png"
    try:
        resolved = path.resolve()
        resolved.relative_to(report_root(data_dir).resolve())
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def list_reports(data_dir: Path, *, limit: int = 60) -> list[ReportEntry]:
    """列出已生成的摘录，按日期倒序。"""
    root = report_root(data_dir)
    if not root.is_dir():
        return []
    entries: list[ReportEntry] = []
    for png in root.glob("*/*.png"):
        parsed = parse_report_stem(png.stem)
        if parsed is None:
            continue
        kind, day = parsed
        try:
            stat = png.stat()
        except OSError:
            continue
        entries.append(
            ReportEntry(
                slug=png.parent.name,
                day=day,
                size=stat.st_size,
                mtime=stat.st_mtime,
                kind=kind,
            )
        )
    entries.sort(key=lambda item: (item.day, item.mtime), reverse=True)
    return entries[:limit] if limit > 0 else entries
