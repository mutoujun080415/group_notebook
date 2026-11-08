"""周报功能测试：周期边界、week 产物、周记提示词、周记渲染与端点。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from group_notebook.collector import monday_of, period_bounds, range_days
from group_notebook.config import GroupNotebookConfig
from group_notebook.models import NotebookStats
from group_notebook.pillow_renderer import PillowNotebookRenderer
from group_notebook.reports import parse_report_stem, report_file
from group_notebook.analyzer import build_prompt, build_fallback_report
from group_notebook.renderer import pick_engine

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


class TestPeriodHelpers:
    def test_monday_of(self):
        # 2026-11-08 是周日，所在周周一是 11-02
        assert monday_of("2026-11-08") == "2026-11-02"
        assert monday_of("2026-11-02") == "2026-11-02"

    def test_range_days(self):
        assert range_days("2026-11-05", "2026-11-07") == [
            "2026-11-05", "2026-11-06", "2026-11-07"]
        assert range_days("2026-11-07", "2026-11-05") == []

    def test_period_bounds_caps_at_now(self):
        import time
        start, end = period_bounds("2026-11-02", 7, "Asia/Shanghai")
        assert end - start <= 7 * 86400
        assert end <= time.time() + 1


class TestParseReportStem:
    def test_day(self):
        assert parse_report_stem("2026-11-05") == ("day", "2026-11-05")

    def test_week(self):
        assert parse_report_stem("week-2026-11-02") == ("week", "2026-11-02")

    def test_invalid(self):
        assert parse_report_stem("week-not-a-day") is None
        assert parse_report_stem("garbage") is None


class TestWeeklyFiles:
    def test_report_file_week_kind(self, tmp_path: Path):
        folder = tmp_path / "reports" / "abc"
        folder.mkdir(parents=True)
        png = folder / "week-2026-11-02.png"
        png.write_bytes(b"png")
        assert report_file(tmp_path, "abc", "2026-11-02", kind="week") == png.resolve()
        # day kind 找不到 week 文件
        assert report_file(tmp_path, "abc", "2026-11-02") is None


class TestWeeklyPrompt:
    def _config(self):
        return SimpleNamespace(
            general=SimpleNamespace(timezone="Asia/Shanghai"),
            analysis=SimpleNamespace(
                model_task="utils", topic_count=4, portrait_count=8, quote_count=3,
                persona="人设", custom_prompt="", llm_timeout_seconds=30,
            ),
            notebook=SimpleNamespace(page_title="周记标题"),
        )

    def _stats(self):
        return NotebookStats(date="2026-11-02", message_count=500, member_count=10,
                             char_count=3000, emoji_count=10,
                             hourly=[0] * 12 + [300] + [0] * 11,
                             top_members=[{"name": "阿鱼", "count": 200}],
                             peak_hour=12, peak_count=300)

    def test_prompt_uses_week_wording(self):
        system_prompt, user_prompt = build_prompt(
            report_date="2026-11-02",
            room_name="测试群",
            bot_name="安安",
            stats=self._stats(),
            transcript="x",
            config=self._config(),
            member_lines="- 阿鱼：200 条",
            period_word="一周",
        )
        assert "把一周的聊天记录整理成一页手账" in system_prompt
        assert "站在一周的尺度上" in system_prompt
        assert "把一周的聊天内容总结成一段话" in user_prompt
        assert "先写他这一周具体做了什么" in user_prompt
        assert "统计周期：2026-11-02" in user_prompt

    def test_fallback_report_week_period(self):
        config = self._config()
        report = build_fallback_report(
            report_date="2026-11-02",
            room_name="测试群",
            bot_name="安安",
            page_title="周记标题",
            stats=self._stats(),
            messages=[
                {"time": 1793520000.0, "sender_name": "阿鱼",
                 "processed_plain_text": "一周里的长发言，足够作为样本使用了"},
            ],
            config=config,
            period_word="一周",
            period_text="2026-11-02 ~ 11-08 · 周记",
        )
        assert report.period_text == "2026-11-02 ~ 11-08 · 周记"
        assert report.recap.startswith("这一周")


class TestWeeklyRender:
    def test_pillow_render_with_period_text(self, tmp_path):
        pytest.importorskip("PIL")
        config = GroupNotebookConfig()
        renderer = PillowNotebookRenderer(config, base_dir=PLUGIN_ROOT)
        from group_notebook.models import (
            Mood, MoodDimension, NotebookReport, Portrait, QuoteItem, Topic,
        )
        stats = NotebookStats(date="2026-11-02", message_count=800, member_count=12,
                              char_count=9000, emoji_count=40,
                              hourly=[0] * 9 + [500, 200] + [0] * 13,
                              top_members=[{"name": "测试员", "count": 300}],
                              peak_hour=9, peak_count=500)
        report = NotebookReport(
            date="2026-11-02", room_name="测试群", bot_name="安安",
            page_title="安安的群聊周记", stats=stats,
            period_text="2026-11-02 ~ 11-08 · 周记",
            greeting="一周的热闹，收进这一页。",
            topics=[Topic(title="周度话题", contributors=["测试员"], detail="[测试员] 干了大事。")],
            recap="这一周整体热闘。",
            portraits=[Portrait(name="测试员", title="龙王", mbti="ENFP", tag="话痨", desc="一周活跃。")],
            quotes=[QuoteItem(sender="测试员", text="周记金句。", comment="锐评。")],
            mood=Mood(title="大晴天", subtitle="副标",
                      dimensions=[MoodDimension(name="共鸣", percentage=100, comment="热。")],
                      summary="总结。"),
            coffee="咖啡。", murmur="晚安。",
            source="llm", generated_at="2026-11-08T22:00:00",
        )
        png = tmp_path / "week.png"
        width, height = renderer.render(report, png)
        assert width == 750 and height > 1200
        assert png.stat().st_size > 10_000


class TestEngineUnchanged:
    def test_pick_engine_default(self):
        cfg = SimpleNamespace(render_engine="auto", browser_channel="msedge")
        assert pick_engine(cfg) in ("playwright", "pillow")


class TestTickSignatures:
    def test_tick_dispatch_signatures_align(self):
        """回归：_tick 调用 _tick_daily(now)/_tick_weekly(now)，签名必须接受 now，
        否则整个定时任务每分钟抛 TypeError（v0.4.1 真机事故）。"""
        import inspect

        from group_notebook.plugin import GroupNotebookPlugin

        def params_of(name: str) -> list[str]:
            func = getattr(GroupNotebookPlugin, name)
            return [p for p in inspect.signature(func).parameters if p != "self"]

        assert params_of("_tick") == []
        assert params_of("_tick_daily") == ["now"]
        assert params_of("_tick_weekly") == ["now"]
