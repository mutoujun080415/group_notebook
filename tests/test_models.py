"""models.py 的数据模型与文本工具测试。"""

from __future__ import annotations

from group_notebook.models import (
    Mood,
    MoodDimension,
    NotebookReport,
    NotebookStats,
    Portrait,
    QuoteItem,
    Topic,
    clamp_text,
    clean_text,
    count_emoji,
    count_visible_chars,
    strip_markup,
)


class TestTextTools:
    def test_clean_text_strips_cq_and_whitespace(self):
        raw = "[CQ:image,file=abc.jpg]  今天\n天气  不错　"
        assert clean_text(raw) == "今天 天气 不错"

    def test_clean_text_truncate(self):
        assert clean_text("一二三四五", 3) == "一二…"
        assert clean_text("一二三四五", 0) == "一二三四五"

    def test_clean_text_none_safe(self):
        assert clean_text(None) == ""

    def test_count_visible_chars_ignores_space_and_cq(self):
        assert count_visible_chars("[CQ:face] a b c") == 3

    def test_count_emoji(self):
        assert count_emoji("哈哈😀!🤔") == 2
        assert count_emoji("[表情]再来一个[动画表情]") == 2
        assert count_emoji("纯文本") == 0

    def test_clamp_text_strips_trailing_punct(self):
        assert clamp_text("abcdef，", 5) == "abcd…"
        assert clamp_text(None, 5) == ""

    def test_strip_markup(self):
        assert strip_markup("**加粗**的话") == "加粗的话"


class TestReportRoundTrip:
    def _report(self) -> NotebookReport:
        stats = NotebookStats(
            date="2026-11-05",
            message_count=100,
            member_count=8,
            char_count=1200,
            emoji_count=10,
            hourly=[0] * 23 + [100],
            top_members=[{"name": "阿鱼", "count": 40}],
            peak_hour=23,
            peak_count=100,
        )
        return NotebookReport(
            date="2026-11-05",
            room_name="测试群",
            bot_name="安安",
            page_title="安安的群聊摘录",
            stats=stats,
            greeting="翻页吧",
            topics=[Topic(title="标题", contributors=["阿鱼", "小别"], detail="细节 [阿鱼] 说了话")],
            recap="今天聊了很多，一句话总结不完。",
            portraits=[Portrait(name="阿鱼", title="龙王", mbti="ENFP", tag="话痨", desc="做了很多事")],
            quotes=[QuoteItem(sender="阿鱼", text="金句", comment="锐评")],
            mood=Mood(
                title="大晴天",
                subtitle="副标",
                dimensions=[MoodDimension(name="共鸣", percentage=60, comment="很热")],
                summary="总结",
            ),
            coffee="来杯拿铁",
            murmur="晚安",
            source="llm",
            generated_at="2026-11-05T23:00:00",
        )

    def test_to_dict_from_dict_roundtrip(self):
        report = self._report()
        data = report.to_dict()
        restored = NotebookReport.from_dict(data)
        assert restored == report

    def test_from_dict_tolerates_missing_fields(self):
        restored = NotebookReport.from_dict({})
        assert restored.date == ""
        assert restored.recap == ""
        assert restored.stats.hourly == [0] * 24
        assert restored.mood.dimensions == []

    def test_from_dict_ignores_bad_items(self):
        data = {
            "topics": ["不是字典", {"title": "ok", "contributors": ["a", "", None], "detail": "d"}],
            "portraits": ["x", {"name": "某人"}],
        }
        restored = NotebookReport.from_dict(data)
        assert len(restored.topics) == 1
        assert restored.topics[0].contributors == ["a"]
        assert len(restored.portraits) == 1
