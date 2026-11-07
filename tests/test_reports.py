"""reports.py 的路径规则与穿越防护测试。"""

from __future__ import annotations

from pathlib import Path

from group_notebook.reports import (
    is_valid_day,
    list_reports,
    report_file,
    slug_for_stream,
)


class TestSlug:
    def test_hex_stream_untouched(self):
        slug = "a" * 64
        assert slug_for_stream(slug) == slug

    def test_special_chars_replaced(self):
        assert slug_for_stream("qq_group:123/456") == "qq_group_123_456"

    def test_empty(self):
        assert slug_for_stream("") == "stream"

    def test_truncated_to_64(self):
        assert len(slug_for_stream("x" * 100)) == 64


class TestIsValidDay:
    def test_valid(self):
        assert is_valid_day("2026-11-05")

    def test_invalid(self):
        assert not is_valid_day("2026-13-01")
        assert not is_valid_day("20261105")
        assert not is_valid_day("")
        assert not is_valid_day("../../etc")


class TestReportFile:
    def test_missing_returns_none(self, tmp_path: Path):
        assert report_file(tmp_path, "abc", "2026-11-05") is None

    def test_traversal_blocked(self, tmp_path: Path):
        # slug 里带路径分隔符时（与清洗结果不一致）必须拒绝
        assert report_file(tmp_path, "../escape", "2026-11-05") is None
        assert report_file(tmp_path, "abc", "not-a-day") is None

    def test_valid_file_resolved(self, tmp_path: Path):
        folder = tmp_path / "reports" / "abc"
        folder.mkdir(parents=True)
        png = folder / "2026-11-05.png"
        png.write_bytes(b"png")
        resolved = report_file(tmp_path, "abc", "2026-11-05")
        assert resolved is not None
        assert resolved == png.resolve()


class TestListReports:
    def test_empty_root(self, tmp_path: Path):
        assert list_reports(tmp_path) == []

    def test_sorted_by_date_desc(self, tmp_path: Path):
        for slug in ("s1", "s2"):
            folder = tmp_path / "reports" / slug
            folder.mkdir(parents=True)
            for day in ("2026-11-04", "2026-11-05"):
                (folder / f"{day}.png").write_bytes(b"x")
        entries = list_reports(tmp_path)
        assert [entry.day for entry in entries] == ["2026-11-05", "2026-11-05", "2026-11-04", "2026-11-04"]

    def test_ignores_bad_names(self, tmp_path: Path):
        folder = tmp_path / "reports" / "s1"
        folder.mkdir(parents=True)
        (folder / "垃圾名字.png").write_bytes(b"x")
        (folder / "2026-11-05.png").write_bytes(b"x")
        entries = list_reports(tmp_path)
        assert len(entries) == 1
