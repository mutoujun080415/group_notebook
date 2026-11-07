"""群聊摘录的数据模型与文本清洗工具。

本模块只依赖标准库，便于在无框架环境（单元测试、离线渲染脚本）中复用。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

_EMOJI_RE = re.compile(
    "[" "\U0001f300-\U0001faff" "\U00002600-\U000027bf" "\U0001f000-\U0001f2ff" "\u2b00-\u2bff" "]+"
)
_CQ_RE = re.compile(r"\[CQ:[^\]]+\]")
_MARKUP_RE = re.compile(r"\*\*(.+?)\*\*")
_SPACE_RE = re.compile(r"\s+")


def clean_text(text: str, limit: int = 0) -> str:
    """清洗单条聊天文本：去 CQ 码、压缩空白，并按需截断。"""
    value = _CQ_RE.sub("", str(text or ""))
    value = value.replace("\u200b", " ").replace("\r", " ").replace("\n", " ")
    value = _SPACE_RE.sub(" ", value).strip()
    if limit > 0 and len(value) > limit:
        value = value[: max(1, limit - 1)].rstrip() + "…"
    return value


def strip_markup(text: str) -> str:
    """移除 ``**加粗**`` 标记，保留纯文本。"""
    return _MARKUP_RE.sub(r"\1", str(text or ""))


def count_visible_chars(text: str) -> int:
    """统计有效字符数（不含空白与 CQ 码）。"""
    value = _CQ_RE.sub("", str(text or ""))
    return len([char for char in value if not char.isspace()])


def count_emoji(text: str) -> int:
    """统计文本中的 emoji 与常见表情占位数量。"""
    value = str(text or "")
    total = sum(len(match.group(0)) for match in _EMOJI_RE.finditer(value))
    for token in ("[表情]", "[动画表情]", "[图片表情]"):
        total += value.count(token)
    return total


def clamp_text(text: Any, limit: int) -> str:
    """把任意值转成不超过 ``limit`` 个字符的单行文本。"""
    if text is None:
        return ""
    value = _SPACE_RE.sub(" ", str(text)).strip()
    if limit > 0 and len(value) > limit:
        value = value[: max(1, limit - 1)].rstrip("，,。.、；;：: ") + "…"
    return value


@dataclass(slots=True)
class NotebookStats:
    """一天的群聊统计结果。"""

    date: str
    message_count: int = 0
    member_count: int = 0
    char_count: int = 0
    emoji_count: int = 0
    bot_message_count: int = 0
    bot_char_count: int = 0
    hourly: list[int] = field(default_factory=lambda: [0] * 24)
    top_members: list[dict[str, Any]] = field(default_factory=list)
    peak_hour: int = 0
    peak_count: int = 0
    avg_chars: float = 0.0
    longest: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为可 JSON 序列化的字典。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NotebookStats":
        """从字典还原统计结果。"""
        stats = cls(date=str(data.get("date") or ""))
        for key in (
            "message_count",
            "member_count",
            "char_count",
            "emoji_count",
            "bot_message_count",
            "bot_char_count",
            "peak_hour",
            "peak_count",
        ):
            setattr(stats, key, int(data.get(key) or 0))
        hourly = data.get("hourly") or []
        stats.hourly = [int(item or 0) for item in list(hourly)[:24]]
        stats.hourly += [0] * (24 - len(stats.hourly))
        stats.top_members = [
            {"name": str(item.get("name") or ""), "count": int(item.get("count") or 0)}
            for item in data.get("top_members") or []
            if isinstance(item, dict)
        ]
        stats.avg_chars = float(data.get("avg_chars") or 0.0)
        longest = data.get("longest") or {}
        stats.longest = (
            {
                "name": str(longest.get("name") or ""),
                "text": str(longest.get("text") or ""),
            }
            if longest
            else {}
        )
        return stats


@dataclass(slots=True)
class Topic:
    """一条今日话题。"""

    title: str = ""
    contributors: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "title": self.title,
            "contributors": list(self.contributors),
            "detail": self.detail,
        }


@dataclass(slots=True)
class Portrait:
    """一张群友侧写卡片。"""

    name: str = ""
    title: str = ""
    mbti: str = ""
    tag: str = ""
    desc: str = ""

    def to_dict(self) -> dict[str, str]:
        """转换为字典。"""
        return {
            "name": self.name,
            "title": self.title,
            "mbti": self.mbti,
            "tag": self.tag,
            "desc": self.desc,
        }


@dataclass(slots=True)
class QuoteItem:
    """一句抄在页角的金句。"""

    sender: str = ""
    text: str = ""
    comment: str = ""

    def to_dict(self) -> dict[str, str]:
        """转换为字典。"""
        return {"sender": self.sender, "text": self.text, "comment": self.comment}


@dataclass(slots=True)
class MoodDimension:
    """聊天天气的一个维度。"""

    name: str = ""
    percentage: int = 0
    comment: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {"name": self.name, "percentage": self.percentage, "comment": self.comment}


@dataclass(slots=True)
class Mood:
    """「这一页的聊天天气」整块内容。"""

    title: str = ""
    subtitle: str = ""
    dimensions: list[MoodDimension] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "dimensions": [item.to_dict() for item in self.dimensions],
            "summary": self.summary,
        }


@dataclass(slots=True)
class NotebookReport:
    """一张「群聊摘录」所需的全部数据。"""

    date: str
    room_name: str
    bot_name: str
    page_title: str
    stats: NotebookStats
    greeting: str = ""
    topics: list[Topic] = field(default_factory=list)
    recap: str = ""
    portraits: list[Portrait] = field(default_factory=list)
    quotes: list[QuoteItem] = field(default_factory=list)
    mood: Mood = field(default_factory=Mood)
    coffee: str = ""
    murmur: str = ""
    source: str = "fallback"
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为可持久化的字典。"""
        return {
            "date": self.date,
            "room_name": self.room_name,
            "bot_name": self.bot_name,
            "page_title": self.page_title,
            "stats": self.stats.to_dict(),
            "greeting": self.greeting,
            "topics": [item.to_dict() for item in self.topics],
            "recap": self.recap,
            "portraits": [item.to_dict() for item in self.portraits],
            "quotes": [item.to_dict() for item in self.quotes],
            "mood": self.mood.to_dict(),
            "coffee": self.coffee,
            "murmur": self.murmur,
            "source": self.source,
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NotebookReport":
        """从持久化字典还原摘录。"""
        mood_raw = data.get("mood") or {}
        mood = Mood(
            title=str(mood_raw.get("title") or ""),
            subtitle=str(mood_raw.get("subtitle") or ""),
            dimensions=[
                MoodDimension(
                    name=str(item.get("name") or ""),
                    percentage=int(item.get("percentage") or 0),
                    comment=str(item.get("comment") or ""),
                )
                for item in mood_raw.get("dimensions") or []
                if isinstance(item, dict)
            ],
            summary=str(mood_raw.get("summary") or ""),
        )
        return cls(
            date=str(data.get("date") or ""),
            room_name=str(data.get("room_name") or ""),
            bot_name=str(data.get("bot_name") or ""),
            page_title=str(data.get("page_title") or ""),
            stats=NotebookStats.from_dict(data.get("stats") or {}),
            greeting=str(data.get("greeting") or ""),
            topics=[
                Topic(
                    title=str(item.get("title") or ""),
                    contributors=[
                        str(name).strip()
                        for name in item.get("contributors") or []
                        if name is not None and str(name).strip()
                    ],
                    detail=str(item.get("detail") or ""),
                )
                for item in data.get("topics") or []
                if isinstance(item, dict)
            ],
            recap=str(data.get("recap") or ""),
            portraits=[
                Portrait(**{key: str(item.get(key) or "") for key in
                            ("name", "title", "mbti", "tag", "desc")})
                for item in data.get("portraits") or []
                if isinstance(item, dict)
            ],
            quotes=[
                QuoteItem(
                    sender=str(item.get("sender") or ""),
                    text=str(item.get("text") or ""),
                    comment=str(item.get("comment") or ""),
                )
                for item in data.get("quotes") or []
                if isinstance(item, dict)
            ],
            mood=mood,
            coffee=str(data.get("coffee") or ""),
            murmur=str(data.get("murmur") or ""),
            source=str(data.get("source") or "fallback"),
            generated_at=str(data.get("generated_at") or ""),
        )
