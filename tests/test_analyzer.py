"""analyzer.py 的 JSON 解析、防幻觉校验与规则回退测试。"""

from __future__ import annotations

from types import SimpleNamespace

from group_notebook.analyzer import (
    _coerce_mood,
    _coerce_portraits,
    _coerce_quotes,
    _coerce_topics,
    _normalize,
    _split_json,
    build_fallback_report,
    build_prompt,
    mood_shares,
)
from group_notebook.models import NotebookStats

TEST_CONFIG = SimpleNamespace(
    general=SimpleNamespace(timezone="Asia/Shanghai"),
    analysis=SimpleNamespace(
        model_task="utils",
        topic_count=4,
        portrait_count=8,
        quote_count=3,
        persona="测试人设",
        custom_prompt="",
        llm_timeout_seconds=30,
    ),
    notebook=SimpleNamespace(page_title="测试摘录"),
)


def _stats(**overrides) -> NotebookStats:
    data = dict(
        date="2026-11-05",
        message_count=100,
        member_count=5,
        char_count=500,
        emoji_count=5,
        hourly=[0] * 10 + [50] + [0] * 3 + [30] + [0] * 9,
        top_members=[{"name": "阿鱼", "count": 40}, {"name": "小别", "count": 20}],
        peak_hour=10,
        peak_count=50,
    )
    data.update(overrides)
    return NotebookStats(**data)


class TestSplitJson:
    def test_plain_json(self):
        assert _split_json('{"a": 1}') == {"a": 1}

    def test_code_fence(self):
        assert _split_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_leading_text(self):
        assert _split_json('好的，以下是结果：{"a": "b"} 请查收') == {"a": "b"}

    def test_invalid(self):
        assert _split_json("没有任何大括号") is None
        assert _split_json("{broken") is None


class TestNormalize:
    def test_strips_punct_and_space(self):
        assert _normalize("你 好，世 界！") == "你好世界"


class TestCoerceTopics:
    def test_unknown_contributor_dropped(self):
        raw = [
            {"title": "话题一", "contributors": ["阿鱼", "不存在的人"], "detail": "细节内容"},
            {"title": "", "detail": "没标题，丢弃"},
            {"title": "话题二", "contributors": "不是列表", "detail": "细节二"},
        ]
        topics = _coerce_topics(raw, known_names=["阿鱼", "小别"], limit=4)
        assert len(topics) == 2
        assert topics[0].contributors == ["阿鱼"]
        assert topics[1].contributors == []

    def test_limit(self):
        raw = [{"title": f"t{i}", "detail": "d"} for i in range(10)]
        assert len(_coerce_topics(raw, known_names=[], limit=3)) == 3


class TestCoercePortraits:
    def test_mbti_sanitized(self):
        raw = [{"name": "阿鱼", "title": "龙王", "mbti": "enfp!!", "tag": "话痨", "desc": "描述"}]
        portraits = _coerce_portraits(raw, known_names=["阿鱼"], limit=4)
        assert portraits[0].mbti == "ENFP"

    def test_unknown_name_rejected(self):
        raw = [{"name": "路人甲", "title": "谁", "desc": "编造的人"}]
        assert _coerce_portraits(raw, known_names=["阿鱼"], limit=4) == []

    def test_duplicate_name_rejected(self):
        raw = [
            {"name": "阿鱼", "desc": "第一条"},
            {"name": "阿 鱼", "desc": "归一化后同名"},
        ]
        portraits = _coerce_portraits(raw, known_names=["阿鱼"], limit=4)
        assert len(portraits) == 1


class TestCoerceQuotes:
    def test_fake_quote_rejected(self):
        raw = [{"sender": "阿鱼", "text": "这句原话根本不存在于记录里", "comment": "锐评"}]
        quotes = _coerce_quotes(
            raw,
            known_names=["阿鱼"],
            candidates=[],
            transcript_normalized=_normalize("阿鱼: 今天天气不错"),
            limit=3,
        )
        assert quotes == []

    def test_real_quote_accepted_with_fallback_fill(self):
        raw = [{"sender": "阿鱼", "text": "今天天气不错", "comment": "很实在的一句"}]
        candidates = [{"name": "小别", "text": "候选金句内容也要够长才行"}]
        quotes = _coerce_quotes(
            raw,
            known_names=["阿鱼", "小别"],
            candidates=candidates,
            transcript_normalized=_normalize("08:00 阿鱼: 今天天气不错；09:00 小别: 候选金句内容也要够长才行"),
            limit=2,
        )
        assert len(quotes) == 2
        assert quotes[0].sender == "阿鱼"
        assert quotes[1].sender == "小别"


class TestCoerceMood:
    def test_percent_clamped_and_rescaled(self):
        raw = {
            "title": "天气",
            "subtitle": "副标",
            "summary": "总结",
            "dimensions": [
                {"name": "甲", "percentage": 80, "comment": "评"},
                {"name": "乙", "percentage": 70, "comment": "评"},
            ],
        }
        mood = _coerce_mood(raw)
        assert sum(item.percentage for item in mood.dimensions) == 100

    def test_negative_and_garbage(self):
        mood = _coerce_mood("not a dict")
        assert mood.dimensions == []
        mood = _coerce_mood({"dimensions": [{"name": "甲", "percentage": "很多", "comment": ""}]})
        assert mood.dimensions == []


class TestMoodShares:
    def test_sums_not_over_100(self):
        shares = mood_shares(_stats())
        assert sum(share for _name, share in shares) <= 100
        assert dict(shares)["高峰浪涌"] == 50


class TestFallbackReport:
    def test_builds_complete_report(self):
        stats = _stats()
        messages = [
            {"time": 1793520000.0, "sender_name": "阿鱼", "processed_plain_text": "今天聊了不少内容啊，这一句足够长了"},
            {"time": 1793523600.0, "sender_name": "小别", "processed_plain_text": "另一句像样的话，也能当金句候选使用"},
        ]
        report = build_fallback_report(
            report_date="2026-11-05",
            room_name="测试群",
            bot_name="安安",
            page_title="测试摘录",
            stats=stats,
            messages=messages,
            config=TEST_CONFIG,
            reason="test",
        )
        assert report.source == "fallback:test"
        assert report.greeting
        assert report.topics
        # 回退总结必须带真实统计数字，而不是空话
        assert report.recap and "100" in report.recap and "5" in report.recap
        assert report.portraits
        assert report.mood.dimensions
        assert report.coffee and report.murmur


class TestBuildPrompt:
    def test_prompt_contains_schema_and_counts(self):
        prompt_system, prompt_user = build_prompt(
            report_date="2026-11-05",
            room_name="测试群",
            bot_name="安安",
            stats=_stats(),
            transcript="08:00 阿鱼: 内容",
            config=TEST_CONFIG,
            member_lines="- 阿鱼：40 条",
        )
        assert "只输出一个 JSON 对象" in prompt_system
        assert "测试人设" in prompt_system
        assert "topics 恰好 4 条" in prompt_user
        assert "portraits 最多 8 条" in prompt_user
        assert "[昵称]" in prompt_user
        assert '"recap"' in prompt_user
        assert "100-160 字" in prompt_user
        assert "测试摘录" in prompt_user

    def test_custom_prompt_appended(self):
        config = SimpleNamespace(**{**TEST_CONFIG.__dict__})
        config.analysis = SimpleNamespace(**{**TEST_CONFIG.analysis.__dict__, "custom_prompt": "要多用颜文字"})
        _system, prompt_user = build_prompt(
            report_date="2026-11-05",
            room_name="群",
            bot_name="安安",
            stats=_stats(),
            transcript="x",
            config=config,
            member_lines="- a：1 条",
        )
        assert "要多用颜文字" in _system
