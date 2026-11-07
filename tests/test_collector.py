"""collector.py 的采集与统计纯函数测试。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from group_notebook.collector import (
    build_transcript,
    compute_stats,
    day_bounds,
    pick_quote_candidates,
    sample_messages,
    split_day_periods,
)


def _msg(sender: str, text: str, hour: int = 10, minute: int = 0, sender_id: str = "") -> dict:
    tz = ZoneInfo("Asia/Shanghai")
    ts = datetime(2026, 11, 5, hour, minute, tzinfo=tz).timestamp()
    return {
        "time": ts,
        "sender_name": sender,
        "sender_id": sender_id or sender,
        "processed_plain_text": text,
    }


class TestDayBounds:
    def test_bounds_cover_full_day(self):
        start, end = day_bounds("2026-11-05", "Asia/Shanghai")
        assert end - start == 86400
        assert datetime.fromtimestamp(start, ZoneInfo("Asia/Shanghai")).hour == 0


class TestSampleMessages:
    def test_no_cap(self):
        messages = [_msg("a", "x"), _msg("b", "y")]
        assert sample_messages(messages, 0) == messages

    def test_uniform_sampling_keeps_order(self):
        messages = [_msg(f"u{i}", "m") for i in range(10)]
        picked = sample_messages(messages, 4)
        assert len(picked) == 4
        indexes = [messages.index(item) for item in picked]
        assert indexes == sorted(indexes)


class TestComputeStats:
    def test_basic_counts(self):
        messages = [
            _msg("阿鱼", "早上好😀", hour=8),
            _msg("阿鱼", "今天天气不错", hour=9),
            _msg("小别", "[CQ:image]看图", hour=10),
        ]
        stats = compute_stats(messages, day="2026-11-05", timezone_name="Asia/Shanghai")
        assert stats.message_count == 3
        assert stats.member_count == 2
        assert stats.emoji_count == 1
        assert stats.hourly[8] == 1
        assert stats.hourly[9] == 1
        assert stats.hourly[10] == 1
        assert stats.peak_hour == 8
        assert stats.peak_count == 1
        assert stats.top_members[0] == {"name": "阿鱼", "count": 2}
        assert stats.char_count == (4 + 6 + 2)

    def test_empty_day(self):
        stats = compute_stats([], day="2026-11-05", timezone_name="Asia/Shanghai")
        assert stats.message_count == 0
        assert stats.peak_count == 0
        assert any(stats.hourly) is False


class TestBuildTranscript:
    def test_format_and_budget(self):
        messages = [
            _msg("阿鱼", "第一条", hour=8, minute=1),
            _msg("小别", "第二条", hour=8, minute=2),
        ]
        transcript = build_transcript(messages, timezone_name="Asia/Shanghai")
        lines = transcript.splitlines()
        assert lines[0] == "08:01 阿鱼: 第一条"
        assert lines[1] == "08:02 小别: 第二条"

    def test_budget_keeps_latest(self):
        messages = [_msg(f"u{i}", "很长的消息内容" * 30, hour=8) for i in range(20)]
        transcript = build_transcript(
            messages, timezone_name="Asia/Shanghai", budget=500
        )
        # 预算被截断时保留的是最靠近当前的消息
        assert "u19" in transcript
        assert "u0:" not in transcript


class TestQuoteCandidates:
    def test_filters_short_and_commands(self):
        messages = [
            _msg("a", "哈哈", hour=8),
            _msg("b", "/help", hour=8),
            _msg("c", "https://example.com 很长很长的链接消息", hour=8),
            _msg("d", "这一句是正经的候选金句，长度也足够入选", hour=8),
        ]
        picked = pick_quote_candidates(messages)
        assert len(picked) == 1
        assert picked[0]["name"] == "d"

    def test_limit_and_dedup(self):
        messages = [_msg(f"u{i}", f"第{i}条候选金句内容" * 3, hour=8) for i in range(5)]
        messages.append(_msg("u0", "第0条候选金句内容" * 3, hour=9))  # 同文重复，应被去重
        picked = pick_quote_candidates(messages, limit=3)
        assert len(picked) == 3
        assert len({item["text"] for item in picked}) == 3


class TestSplitDayPeriods:
    def test_four_buckets(self):
        messages = [
            _msg("a", "凌晨", hour=2),
            _msg("b", "上午", hour=9),
            _msg("c", "下午", hour=15),
            _msg("d", "晚上", hour=21),
        ]
        periods = split_day_periods(messages, "Asia/Shanghai")
        labels = [period["label"] for period in periods]
        assert labels == ["凌晨的纸页", "上午的纸页", "下午的纸页", "晚上的纸页"]
