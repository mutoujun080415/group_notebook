"""双引擎切换与 Pillow 渲染器测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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
from group_notebook.renderer import pick_engine, playwright_channel_available
from group_notebook.pillow_renderer import (
    PillowNotebookRenderer,
    _split_mentions,
    strip_emoji,
)

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def _report() -> NotebookReport:
    stats = NotebookStats(
        date="2026-11-07",
        message_count=120,
        member_count=9,
        char_count=2000,
        emoji_count=15,
        hourly=[0] * 8 + [30, 90, 40] + [0] * 13,
        top_members=[{"name": "测试员", "count": 60}],
        peak_hour=9,
        peak_count=90,
    )
    return NotebookReport(
        date="2026-11-07",
        room_name="测试群",
        bot_name="安安",
        page_title="安安的群聊摘录",
        stats=stats,
        greeting="双引擎验证页。",
        topics=[Topic(title="双引擎测试", contributors=["测试员"],
                      detail="这一格来自 [测试员] 的 Pillow 渲染验证 😀。")],
        recap="Pillow 引擎的整段总结，验证折行与胶囊渲染。",
        portraits=[Portrait(name="测试员", title="龙王", mbti="ENFP", tag="话痨",
                            desc="触发 Pillow 渲染验证。")],
        quotes=[QuoteItem(sender="测试员", text="Pillow 也能画手账。", comment="记录一下。")],
        mood=Mood(title="大晴天", subtitle="验证",
                  dimensions=[MoodDimension(name="验证", percentage=70, comment="通过。"),
                              MoodDimension(name="水群", percentage=30, comment="正常。")],
                  summary="双引擎渲染成功。"),
        coffee="来杯咖啡。", murmur="晚安。",
        source="llm", generated_at="2026-11-07T23:00:00",
    )


class TestStripEmoji:
    def test_strips(self):
        assert strip_emoji("哈哈😀!") == "哈哈!"
        assert strip_emoji("纯文本") == "纯文本"


class TestSplitMentions:
    def test_known_mention(self):
        segs = _split_mentions("来自 [阿鱼] 的话", ["阿鱼"])
        assert ("m", "阿鱼") in segs
        assert all(kind != "m" or value == "阿鱼" for kind, value in segs)

    def test_unknown_kept_as_text(self):
        segs = _split_mentions("来自 [路人] 的话", ["阿鱼"])
        assert ("m", "路人") not in segs


class TestPickEngine:
    def _cfg(self, engine: str, channel: str = "msedge") -> SimpleNamespace:
        return SimpleNamespace(render_engine=engine, browser_channel=channel)

    def test_explicit_pillow(self):
        assert pick_engine(self._cfg("pillow")) == "pillow"

    def test_explicit_playwright(self):
        assert pick_engine(self._cfg("playwright")) == "playwright"

    def test_auto_without_browser_falls_back(self, monkeypatch):
        monkeypatch.setattr("group_notebook.renderer.playwright_channel_available",
                            lambda channel: False)
        assert pick_engine(self._cfg("auto")) == "pillow"

    def test_auto_with_browser(self, monkeypatch):
        monkeypatch.setattr("group_notebook.renderer.playwright_channel_available",
                            lambda channel: True)
        assert pick_engine(self._cfg("auto")) == "playwright"

    def test_default_is_auto(self):
        cfg = GroupNotebookConfig()
        assert cfg.notebook.render_engine == "auto"

    def test_edge_detection_on_real_machine(self):
        # 本机装了 Edge 就应判定可用；没有也不抛错
        result = playwright_channel_available("msedge")
        assert isinstance(result, bool)


class TestPillowRender:
    def test_render_smoke(self, tmp_path):
        pytest.importorskip("PIL")
        config = GroupNotebookConfig()
        renderer = PillowNotebookRenderer(config, base_dir=PLUGIN_ROOT)
        png = tmp_path / "out.png"
        width, height = renderer.render(_report(), png)
        assert width == 750
        assert height > 1200
        assert png.is_file() and png.stat().st_size > 10_000

    def test_render_with_emoji_and_long_text(self, tmp_path):
        pytest.importorskip("PIL")
        config = GroupNotebookConfig()
        renderer = PillowNotebookRenderer(config, base_dir=PLUGIN_ROOT)
        report = _report()
        report.topics[0].detail = "超长文本 " * 60 + "😀 结尾"
        report.murmur = "带 😀 的碎碎念"
        png = tmp_path / "out2.png"
        width, height = renderer.render(report, png)
        assert png.is_file() and height > 1200
