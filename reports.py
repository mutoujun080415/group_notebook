"""摘录产物的路径规则与历史列表。

文件名约定（stem 部分）：
- 日报：``YYYY-MM-DD``
- 周记：``week-<起>_<止>``（旧版 ``week-<起>`` 兼容读取，视为单日区间）
- 案底：``dossier-<起>_<止>-<焦点指纹>``（焦点指纹为 4-12 位字母数字，
  由方向+聚焦成员哈希而来；旧版 ``dossier-<起>`` 兼容读取）

只依赖标准库，WebUI 的 Router、Service 与测试共用同一套路径解析。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path

_SLUG_PATTERN = re.compile(r"[^0-9A-Za-z_-]+")
_STEM_RE = re.compile(
    r"^(?P<prefix>week-|dossier-)?"
    r"(?P<start>\d{4}-\d{2}-\d{2})"
    r"(?:_(?P<end>\d{4}-\d{2}-\d{2}))?"
    r"(?:-(?P<key>[0-9A-Za-z]{4,12}))?$"
)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DEFAULT_BASE = "/plugins/group-notebook"


def slug_for_stream(stream_id: str) -> str:
    """把聊天流 ID 转成可安全用作目录名的标识。"""
    cleaned = _SLUG_PATTERN.sub("_", str(stream_id or "")).strip("_")
    return (cleaned or "stream")[:64]


def is_valid_day(value: str) -> bool:
    """判断字符串是否是合法的 ``YYYY-MM-DD`` 日期。"""
    text = str(value or "")
    if not _DATE_RE.match(text):
        return False
    try:
        date_cls.fromisoformat(text)
    except ValueError:
        return False
    return True


def build_stem(kind: str, start: str, end: str = "", key: str = "") -> str:
    """按类型与起止日期构建产物文件名 stem。

    Args:
        kind: ``day`` / ``week`` / ``dossier``。
        start: 起始日期。
        end: 截止日期；空或等于 start 时不追加。
        key: 案底的焦点指纹（仅 dossier 使用）。

    Returns:
        文件名 stem，如 ``2026-11-07``、``week-2026-11-02_2026-11-08``、
        ``dossier-2026-11-02_2026-11-08-a1b2c3d4``。
    """
    stem = str(start)
    if kind != "day":
        stem = f"{kind}-{stem}"
    if end and end != start and is_valid_day(end):
        stem += f"_{end}"
    if kind == "dossier" and key:
        stem += f"-{key}"
    return stem


def parse_report_stem(stem: str) -> tuple[str, str, str] | None:
    """解析产物文件名，返回 ``(kind, start, end)``；非法命名返回 None。

    兼容旧命名：``week-<起>`` / ``dossier-<起>`` 视为单日区间。
    """
    match = _STEM_RE.match(str(stem or ""))
    if match is None:
        return None
    prefix = match.group("prefix") or ""
    start = match.group("start")
    end = match.group("end") or start
    kind = {"week-": "week", "dossier-": "dossier"}.get(prefix, "day")
    try:
        date_cls.fromisoformat(start)
        date_cls.fromisoformat(end)
    except ValueError:
        return None
    if end < start:
        return None
    return (kind, start, end)


@dataclass(frozen=True, slots=True)
class ReportEntry:
    """一份已生成摘录的索引信息。kind: day / week / dossier。"""

    slug: str
    stem: str
    kind: str
    day: str
    day_end: str
    size: int
    mtime: float

    @property
    def mtime_text(self) -> str:
        """生成时间的可读文本。"""
        return datetime.fromtimestamp(self.mtime).strftime("%Y-%m-%d %H:%M")

    @property
    def period_text(self) -> str:
        """区间展示文本。"""
        if self.day_end and self.day_end != self.day:
            return f"{self.day} ~ {self.day_end[5:]}"
        return self.day

    def to_dict(self) -> dict[str, object]:
        """转换为 JSON 友好的字典。"""
        return {
            "slug": self.slug,
            "stem": self.stem,
            "date": self.day,
            "date_end": self.day_end,
            "kind": self.kind,
            "period_text": self.period_text,
            "size": self.size,
            "mtime": self.mtime,
            "mtime_text": self.mtime_text,
            "url": report_url(self.slug, self.stem),
        }


def report_url(slug: str, stem: str, base: str = _DEFAULT_BASE) -> str:
    """返回独立页面里可访问的海报地址。"""
    return f"{base.rstrip('/')}/api/image?slug={slug}&stem={stem}"


def report_root(data_dir: Path) -> Path:
    """返回摘录根目录。"""
    return Path(data_dir) / "reports"


def report_file(data_dir: Path, slug: str, stem: str) -> Path | None:
    """解析一份摘录的 PNG 路径，路径不合法或不存在时返回 None。

    路径穿越防护：stem 必须能通过命名解析（拒绝对象 ``..`` 等），
    slug 重新清洗后一致，resolve 后仍位于产物根目录之内。
    """
    safe_slug = slug_for_stream(slug)
    if safe_slug != slug or parse_report_stem(stem) is None:
        return None
    path = report_root(data_dir) / safe_slug / f"{stem}.png"
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
        kind, day, day_end = parsed
        try:
            stat = png.stat()
        except OSError:
            continue
        entries.append(
            ReportEntry(
                slug=png.parent.name,
                stem=png.stem,
                kind=kind,
                day=day,
                day_end=day_end,
                size=stat.st_size,
                mtime=stat.st_mtime,
            )
        )
    entries.sort(key=lambda item: (item.day, item.day_end, item.mtime), reverse=True)
    return entries[:limit] if limit > 0 else entries
