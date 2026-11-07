"""renderer.py 的上下文组装与模板渲染测试（截图部分见 demo 脚本）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from group_notebook.config import GroupNotebookConfig
from group_notebook.models import (
    Mood,
    MoodDimension,
    NotebookReport,
    NotebookStats,
    Portrait,
    QuoteItem,
    Topic,
)
from group_notebook.renderer import NotebookRenderer, color_for, initial_for, rich_html

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def _report() -> NotebookReport:
    stats = NotebookStats(
        date="2026-11-05",
        message_count=300,
        member_count=24,
        char_count=3577,
        emoji_count=27,
        hourly=[8, 7, 5, 0, 3, 0, 2, 0, 11, 20, 130, 12, 7, 9, 6, 8, 5, 12, 10, 14, 13, 14, 11, 6],
        top_members=[{"name": "龙套先生", "count": 52}],
        peak_hour=10,
        peak_count=130,
    )
    return NotebookReport(
        date="2026-11-05",
        room_name="测试群",
        bot_name="安安",
        page_title="安安的群聊摘录",
        stats=stats,
        greeting="你们聊过的事情，都记在这里了。",
        topics=[
            Topic(title="话题标题", contributors=["龙套先生"], detail="细节 [龙套先生] 说了话 [不认识]")
        ],
        recap="上午聊强度，下午聊清理，深夜电台照常营业，热闹但不吵的一天。",
        portraits=[Portrait(name="龙套先生", title="龙王", mbti="ENFP", tag="话痨", desc="描述")],
        quotes=[QuoteItem(sender="龙套先生", text="金句", comment="锐评 [龙套先生]")],
        mood=Mood(
            title="大晴天",
            subtitle="副标",
            dimensions=[
                MoodDimension(name="共鸣", percentage=60, comment="热"),
                MoodDimension(name="水群", percentage=40, comment="水"),
            ],
            summary="总结一句",
        ),
        coffee="拿铁",
        murmur="晚安",
        source="llm",
        generated_at="2026-11-05T23:30:00",
    )


class TestHelpers:
    def test_color_stable(self):
        assert color_for("阿鱼") == color_for("阿鱼")
        assert color_for("阿鱼") in {c for c in [
            "#ffb74d", "#ff8a65", "#ba68c8", "#4db6ac",
            "#4dd0e1", "#64b5f6", "#81c784", "#e57373",
        ]}

    def test_initial(self):
        assert initial_for("阿鱼") == "阿"
        assert initial_for("  ") == "?"
        assert initial_for("") == "?"


class TestRichHtml:
    def test_escapes_html(self):
        assert rich_html("<script>&", []) == "&lt;script&gt;&amp;"

    def test_known_mention_becomes_capsule(self):
        html = rich_html("细节 [阿鱼] 说话", ["阿鱼"])
        assert '<span class="mention">@阿鱼</span>' in html

    def test_unknown_mention_kept_as_text(self):
        html = rich_html("细节 [不认识] 说话", ["阿鱼"])
        assert "mention" not in html
        assert "[不认识]" in html


class TestBuildContext:
    def test_context_complete(self):
        config = GroupNotebookConfig()
        renderer = NotebookRenderer(config, base_dir=PLUGIN_ROOT)
        context = renderer.build_context(_report())

        assert context["page_title"] == "安安的群聊摘录"
        assert context["peak_text"] == "10:00 - 11:00"
        assert len(context["hourly"]) == 24
        peak_bar = next(item for item in context["hourly"] if item["peak"])
        assert peak_bar["hour"] == 10
        assert peak_bar["count"] == 130

        topic = context["topics"][0]
        assert topic["index"] == 1
        # 参与者原样渲染（过滤是 analyzer 的职责，见 test_analyzer）
        assert [item["name"] for item in topic["contributors"]] == ["龙套先生"]
        assert "mention" in topic["detail_html"]
        assert "[不认识]" in topic["detail_html"]

        portrait = context["portraits"][0]
        assert portrait["tilt"]
        assert portrait["initial"] == "龙"

        quote = context["quotes"][0]
        assert "mention" in quote["comment_html"]
        assert quote["alt"] is False

        mood = context["mood"]
        assert mood["bar"] == [
            {"name": "共鸣", "pct": 60, "color": "#ffb74d"},
            {"name": "水群", "pct": 40, "color": "#ff8a65"},
        ]
        assert mood["has_dims"] is True

        assert context["coffee"]["enabled"] is True
        assert context["coffee"]["title"] == "安安的咖啡推荐"
        assert context["murmur"]["enabled"] is True
        assert context["recap"]["enabled"] is True
        assert context["recap"]["title"] == "今日总结"
        assert context["date_text"].startswith("2026 年 11 月 5 日")
        assert "星期" in context["date_text"]

    def test_coffee_disabled(self):
        config = GroupNotebookConfig()
        config.notebook.coffee_enabled = False
        renderer = NotebookRenderer(config, base_dir=PLUGIN_ROOT)
        context = renderer.build_context(_report())
        assert context["coffee"]["enabled"] is False

    def test_page_title_auto_generated(self):
        config = GroupNotebookConfig()
        assert config.resolved_page_title() == "安安的群聊摘录"
        config.notebook.bot_name = "小狐"
        assert config.resolved_page_title() == "小狐的群聊摘录"


class TestRenderHtml:
    def test_render_contains_sections(self):
        pytest.importorskip("jinja2")
        config = GroupNotebookConfig()
        renderer = NotebookRenderer(config, base_dir=PLUGIN_ROOT)
        html = renderer.render_html(_report())
        for marker in (
            "安安的群聊摘录",
            "GROUP NOTEBOOK",
            "哪一页最热闹",
            "值得夹个书签的话题",
            "今日总结",
            "recap-text",
            "群友的这一页侧写",
            "这几句，抄在页角",
            "这一页的聊天天气",
            "安安的咖啡推荐",
            "安安的碎碎念",
            "龙套先生",
            "crayon-bar peak",
            "mention",
        ):
            assert marker in html, f"模板缺少片段：{marker}"

    def test_render_escapes_user_text(self):
        pytest.importorskip("jinja2")
        config = GroupNotebookConfig()
        renderer = NotebookRenderer(config, base_dir=PLUGIN_ROOT)
        report = _report()
        report.topics[0].detail = "危险 <img src=x onerror=alert(1)> 文本"
        html = renderer.render_html(report)
        assert "<img src=x" not in html
        assert "&lt;img" in html
