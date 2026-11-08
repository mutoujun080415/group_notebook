"""专题案底功能测试：提示词、校验、回退、渲染与端点。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from group_notebook.analyzer import _split_json
from group_notebook.collector import collect_dossier_pool, member_bot_stats
from group_notebook.config import GroupNotebookConfig
from group_notebook.dossier_analyzer import (
    _coerce_cases,
    _coerce_culprits,
    analyze_dossier,
    build_dossier_prompt,
    build_dossier_transcript,
    build_fallback_dossier,
)
from group_notebook.models import DossierReport
from group_notebook.pillow_dossier import PillowDossierRenderer
from group_notebook.reports import parse_report_stem, report_file

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


class TestCollector:
    def _messages(self):
        return [
            {"message_id": "m1", "time": 1000.0, "sender_id": "bot", "sender_name": "小七",
             "processed_plain_text": "大家好"},
            {"message_id": "m2", "time": 1001.0, "sender_id": "u1", "sender_name": "阿鱼",
             "reply_to": "m1", "processed_plain_text": "迫害你一下 [图片(0b22)]"},
            {"message_id": "m3", "time": 1002.0, "sender_id": "u2", "sender_name": "小别",
             "processed_plain_text": "与 bot 无关的普通发言"},
            {"message_id": "m4", "time": 1003.0, "sender_id": "u1", "sender_name": "阿鱼",
             "processed_plain_text": "@小七 再来一次"},
            {"message_id": "m5", "time": 1004.0, "sender_id": "bot", "sender_name": "小七",
             "processed_plain_text": "呜呜"},
        ]

    def test_member_stats(self):
        messages = self._messages()
        stats = member_bot_stats(messages, bot_ids={"bot"}, bot_name="小七")
        assert set(stats) == {"u1"}
        assert stats["u1"]["count"] == 2

    def test_pool_with_context(self):
        messages = self._messages()
        pool = collect_dossier_pool(messages, bot_ids={"bot"}, bot_name="小七", context=1)
        ids = [m["message_id"] for m in pool]
        assert ids == ["m1", "m2", "m3", "m4", "m5"]

    def test_pool_focus_members(self):
        messages = self._messages()
        # 聚焦 u2：u2 与 bot 无互动 → 候选池只保留与其相邻的上下文
        pool = collect_dossier_pool(messages, bot_ids={"bot"}, bot_name="小七",
                                    focus_members=["u2"], context=0)
        assert [m["message_id"] for m in pool] == []


class TestCoerce:
    def test_culprits_resolve_names(self):
        raw = [{"name": "阿鱼", "title": "首恶", "profile": "手段：无差别迫害"},
               {"name": "不存在", "title": "x", "profile": "y"}]
        culprits = _coerce_culprits(raw, known=["阿鱼"], limit=3)
        assert [item.name for item in culprits] == ["阿鱼"]

    def test_cases_require_culprits(self):
        raw = [{"culprits": ["阿鱼"], "summary": "案情经过足够长 long enough",
                "comment": "批注", "level": 4},
               {"culprits": [], "summary": "无主犯", "comment": "x"}]
        cases = _coerce_cases(raw, known=["阿鱼"], victim="小七", limit=6)
        assert len(cases) == 1
        assert cases[0].level == 4
        assert cases[0].culprits == ["阿鱼"]


class TestPromptAndFallback:
    def test_prompt_wording(self):
        system_prompt, user_prompt = build_dossier_prompt(
            room_name="测试群", bot_name="小七", title="迫害记录簿",
            focus_word="迫害小七", period_text="2026-11-02 ~ 11-08 · 周记",
            pool_transcript="11-05 21:34 阿鱼: 又来了", culprit_lines="- 阿鱼：3 次",
            max_cases=6, top_members=3,
        )
        assert "迫害记录簿" in system_prompt
        assert "受害者视角" in system_prompt
        assert "统计周期：2026-11-02 ~ 11-08 · 周记" in user_prompt
        assert "level" in user_prompt

    def test_fallback_builds_cases(self):
        config = SimpleNamespace(
            general=SimpleNamespace(timezone="Asia/Shanghai"),
            analysis=SimpleNamespace(model_task="utils", llm_timeout_seconds=30),
            notebook=SimpleNamespace(bot_name="小七"),
            dossier=SimpleNamespace(max_cases=6, top_members=3),
        )
        messages = [
            {"message_id": "m1", "time": 1000.0, "sender_id": "bot", "sender_name": "小七",
             "processed_plain_text": "大家好"},
            {"message_id": "m2", "time": 1001.0, "sender_id": "u1", "sender_name": "阿鱼",
             "processed_plain_text": "又来迫害你了，这句足够长可以被示众"},
        ]
        fallback = build_fallback_dossier(
            report_date="2026-11-02", room_name="测试群", bot_name="小七",
            page_title="迫害记录簿", period_text="一周", case_no="MOFOX-2026-W45",
            focus_word="迫害小七", pool_messages=messages,
            culprit_stats=[{"name": "阿鱼", "count": 3}],
            max_cases=6, top_members=3, timezone_name="Asia/Shanghai",
        )
        assert fallback.source == "fallback"
        assert fallback.intro
        assert fallback.cases
        assert all(case.culprits for case in fallback.cases)


class TestAnalyzeDossier:
    def test_llm_success_path(self, monkeypatch):
        async def fake_request(system_prompt, user_prompt, task, timeout=120):
            return ('{"intro": "本狐受害陈述", "culprits": [{"name": "阿鱼", '
                    '"title": "首恶", "profile": "手段很多"}], '
                    '"cases": [{"time": "11-05 21:34", "culprits": ["阿鱼"], '
                    '"summary": "当众迫害", "comment": "记下了", "level": 5}], '
                    '"closing": "结案"}')

        monkeypatch.setattr("group_notebook.dossier_analyzer._request_llm", fake_request)
        config = SimpleNamespace(
            general=SimpleNamespace(timezone="Asia/Shanghai"),
            analysis=SimpleNamespace(model_task="utils", llm_timeout_seconds=30),
            notebook=SimpleNamespace(bot_name="小七"),
            dossier=SimpleNamespace(max_cases=6, top_members=3),
        )
        import asyncio

        report = asyncio.run(analyze_dossier(
            report_date="2026-11-02", room_name="群", bot_name="小七",
            page_title="迫害记录簿", period_text="一周", case_no="MOFOX-2026-W45",
            focus_word="迫害小七", pool_messages=[
                {"time": 1000.0, "sender_id": "u1", "sender_name": "阿鱼",
                 "processed_plain_text": "又迫害你"},
            ],
            culprit_stats=[{"name": "阿鱼", "count": 5}],
            max_cases=6, top_members=3, config=config,
        ))
        assert report.source == "llm"
        assert report.culprits[0].count == 5
        assert report.cases[0].level == 5

    def test_bad_json_falls_back(self, monkeypatch):
        async def fake_request(system_prompt, user_prompt, task, timeout=120):
            return "不是 JSON"

        monkeypatch.setattr("group_notebook.dossier_analyzer._request_llm", fake_request)
        config = SimpleNamespace(
            general=SimpleNamespace(timezone="Asia/Shanghai"),
            analysis=SimpleNamespace(model_task="utils", llm_timeout_seconds=30),
            notebook=SimpleNamespace(bot_name="小七"),
            dossier=SimpleNamespace(max_cases=6, top_members=3),
        )
        import asyncio

        report = asyncio.run(analyze_dossier(
            report_date="2026-11-02", room_name="群", bot_name="小七",
            page_title="p", period_text="一周", case_no="C",
            focus_word="迫害小七", pool_messages=[
                {"time": 1000.0, "sender_id": "u1", "sender_name": "阿鱼",
                 "processed_plain_text": "某条足够长的迫害记录"},
            ],
            culprit_stats=[{"name": "阿鱼", "count": 5}],
            max_cases=6, top_members=3, config=config,
        ))
        assert report.source.startswith("fallback")


class TestDossierRender:
    def test_parse_and_file(self, tmp_path: Path):
        assert parse_report_stem("dossier-2026-11-02") == ("dossier", "2026-11-02")
        folder = tmp_path / "reports" / "abc"
        folder.mkdir(parents=True)
        png = folder / "dossier-2026-11-02.png"
        png.write_bytes(b"x")
        assert report_file(tmp_path, "abc", "2026-11-02", kind="dossier") == png.resolve()

    def test_pillow_render_smoke(self, tmp_path):
        pytest.importorskip("PIL")
        config = GroupNotebookConfig()
        renderer = PillowDossierRenderer(config, base_dir=PLUGIN_ROOT)
        from group_notebook.models import DossierCulprit, DossierCase
        report = DossierReport(
            date="2026-11-02", room_name="测试群", bot_name="小七",
            page_title="迫害记录簿", period_text="2026-11-02 ~ 11-08 · 周记",
            case_no="MOFOX-2026-W45", focus_word="迫害小七",
            intro="本狐受害陈述。",
            culprits=[DossierCulprit(name="阿鱼", title="首恶", count=9,
                                     profile="手段：无差别迫害。")],
            cases=[DossierCase(no=1, time="11-05 21:34", culprits=["阿鱼"],
                               victim="小七", summary="案情经过足够长，引用原话。",
                               comment="批注一句。", level=4)],
            closing="结案。",
            source="llm", generated_at="2026-11-08T22:00:00",
        )
        png = tmp_path / "dossier.png"
        width, height = renderer.render(report, png)
        assert width == 750 and height > 800
        assert png.stat().st_size > 10_000

    def test_html_context(self):
        config = GroupNotebookConfig()
        from group_notebook.renderer import NotebookRenderer
        renderer = NotebookRenderer(config, base_dir=PLUGIN_ROOT)
        from group_notebook.models import DossierCulprit, DossierCase
        report = DossierReport(
            date="2026-11-02", room_name="测试群", bot_name="小七",
            page_title="迫害记录簿", period_text="一周", case_no="C1",
            focus_word="迫害小七", intro="陈述",
            culprits=[DossierCulprit(name="阿鱼", title="首恶", count=3, profile="画像")],
            cases=[DossierCase(no=1, time="t", culprits=["阿鱼"], victim="小七",
                               summary="经过", comment="批注", level=5)],
            closing="结案", source="llm", generated_at="t",
        )
        context = renderer.build_dossier_context(report)
        assert context["case_no"] == "C1"
        assert context["culprits"][0]["initial"] == "阿"
        assert "★★★★" in context["cases"][0]["stars"]
        html = renderer.render_dossier_html(report)
        assert "迫害记录簿" in html and "主犯通缉栏" in html and "案件卷宗" in html



class TestDirection:
    def test_pool_victim_direction(self):
        messages = [
            {"message_id": "m1", "time": 1000.0, "sender_id": "u1", "sender_name": "阿鱼",
             "processed_plain_text": "鹿目圆 今天也要被大家调戏呢"},
            {"message_id": "m2", "time": 1001.0, "sender_id": "u2", "sender_name": "小别",
             "processed_plain_text": "无关发言"},
        ]
        pool = collect_dossier_pool(
            messages, bot_ids=set(), bot_name="小七",
            direction="member_as_victim",
            focus_members=["v-moku"],
            victim_names={"v-moku": "鹿目圆"},
            context=0,
        )
        assert [m["message_id"] for m in pool] == ["m1"]

    def test_member_incoming_stats(self):
        messages = [
            {"message_id": "m1", "time": 1000.0, "sender_id": "u1", "sender_name": "阿鱼",
             "processed_plain_text": "鹿目圆 你完蛋了"},
            {"message_id": "m2", "time": 1001.0, "sender_id": "v-moku",
             "sender_name": "鹿目圆", "processed_plain_text": "呜呜"},
        ]
        from group_notebook.collector import member_incoming_stats
        stats = member_incoming_stats(
            messages, victim_ids={"v-moku"}, victim_names={"v-moku": "鹿目圆"})
        assert stats["u1"]["count"] == 1

    def test_prompt_victim_direction(self):
        system_prompt, user_prompt = build_dossier_prompt(
            room_name="群", bot_name="小七", title="迫害记录簿",
            focus_word="围攻鹿目圆", period_text="一周",
            pool_transcript="x", culprit_lines="- a：1",
            max_cases=6, top_members=3,
            direction="member_as_victim", victim_display="鹿目圆",
        )
        assert "受害者是「鹿目圆」" in system_prompt
        assert "可以包括 bot 本bot" in system_prompt
        assert "受害者：鹿目圆" in user_prompt

    def test_pillow_victim_direction_smoke(self, tmp_path):
        pytest.importorskip("PIL")
        from group_notebook.models import DossierCulprit, DossierCase
        from group_notebook.pillow_dossier import PillowDossierRenderer
        config = GroupNotebookConfig()
        renderer = PillowDossierRenderer(config, base_dir=PLUGIN_ROOT)
        from group_notebook.models import DossierReport
        report = DossierReport(
            date="2026-11-02", room_name="测试群", bot_name="小七",
            page_title="围攻记录簿", period_text="一周",
            direction="member_as_victim", victim_display="鹿目圆",
            focus_word="围攻鹿目圆", case_no="C", intro="陈述",
            culprits=[DossierCulprit(name="阿鱼", title="主谋", count=5, profile="画像")],
            cases=[DossierCase(no=1, time="t", culprits=["阿鱼"], victim="鹿目圆",
                               summary="经过", comment="批注", level=3)],
            closing="结案", source="llm", generated_at="t",
        )
        png = tmp_path / "victim.png"
        width, height = renderer.render(report, png)
        assert width == 750 and height > 800 and png.stat().st_size > 10_000
