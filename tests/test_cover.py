"""cover.py 的封面存储测试（全部落到 tmp_path，不污染工作目录）。"""

from __future__ import annotations

import pytest

from group_notebook import cover

PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture()
def cover_dir(tmp_path, monkeypatch):
    """把封面存储重定向到临时目录。"""
    monkeypatch.setattr(cover, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cover, "COVER_DIR", tmp_path / "covers")
    monkeypatch.setattr(cover, "META_FILE", tmp_path / "cover.json")
    return tmp_path


class TestSniff:
    def test_png(self):
        assert cover.sniff_image_ext(PNG_1PX) == "png"

    def test_jpeg(self):
        assert cover.sniff_image_ext(b"\xff\xd8\xff\xe0" + b"\x00" * 20) == "jpg"

    def test_webp(self):
        assert cover.sniff_image_ext(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"

    def test_riff_but_not_webp(self):
        assert cover.sniff_image_ext(b"RIFF\x00\x00\x00\x00WAVEfmt ") is None

    def test_unknown(self):
        assert cover.sniff_image_ext(b"not an image at all") is None


class TestSaveUpload:
    def test_save_and_load(self, cover_dir):
        meta = cover.save_upload(PNG_1PX)
        assert meta["path"] == "covers/cover.png"
        assert cover.image_path() is not None
        assert cover.load_meta()["focus"] == "center"
        assert (cover_dir / "covers" / "cover.png").is_file()

    def test_reupload_replaces_old_format(self, cover_dir):
        cover.save_upload(PNG_1PX)
        meta = cover.save_upload(b"\xff\xd8\xff" + b"\x00" * 20)
        assert meta["path"] == "covers/cover.jpg"
        assert not (cover_dir / "covers" / "cover.png").exists()

    def test_empty_rejected(self, cover_dir):
        with pytest.raises(ValueError, match="没有收到"):
            cover.save_upload(b"")

    def test_too_large_rejected(self, cover_dir, monkeypatch):
        monkeypatch.setattr(cover, "MAX_BYTES", 10)
        with pytest.raises(ValueError, match="太大"):
            cover.save_upload(PNG_1PX)

    def test_not_image_rejected(self, cover_dir):
        with pytest.raises(ValueError, match="图片格式"):
            cover.save_upload(b"plain text, definitely not an image")

    def test_previous_params_kept(self, cover_dir):
        cover.save_upload(PNG_1PX)
        cover.update(focus="top", height=340)
        cover.save_upload(PNG_1PX)
        meta = cover.load_meta()
        assert meta["focus"] == "top"
        assert meta["height"] == 340


class TestUpdateAndClear:
    def test_update_without_cover(self, cover_dir):
        with pytest.raises(ValueError, match="还没有上传"):
            cover.update(focus="top")

    def test_update_clamps_height(self, cover_dir):
        cover.save_upload(PNG_1PX)
        meta = cover.update(height=9999)
        assert meta["height"] == cover.MAX_HEIGHT
        meta = cover.update(height=1)
        assert meta["height"] == cover.MIN_HEIGHT

    def test_update_invalid_focus_falls_back(self, cover_dir):
        cover.save_upload(PNG_1PX)
        meta = cover.update(focus="somewhere-else")
        assert meta["focus"] == cover.DEFAULT_FOCUS

    def test_clear(self, cover_dir):
        cover.save_upload(PNG_1PX)
        cover.clear()
        assert cover.image_path() is None
        assert cover.load_meta() == {}


class TestFocusCss:
    def test_mapping(self):
        assert cover.focus_to_css("top-left") == "0% 0%"
        assert cover.focus_to_css("center") == "50% 50%"
        assert cover.focus_to_css("bottom-right") == "100% 100%"
        assert cover.focus_to_css("unknown") == "50% 50%"
