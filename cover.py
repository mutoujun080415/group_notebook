"""封面（壁纸/立绘）存储。

用户在 WebUI 上传的封面放在 ``data/group_notebook/covers/``，元信息（焦点、
高度、更新时间）写在 ``data/group_notebook/cover.json``。渲染时把元信息转成
CSS 的 ``object-fit: cover`` + ``object-position``，换焦点/高度不需要重新上传，
也不依赖 Pillow——图片格式用魔数校验，裁切交给浏览器。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

DATA_DIR = Path("data") / "group_notebook"
COVER_DIR = DATA_DIR / "covers"
META_FILE = DATA_DIR / "cover.json"

MAX_BYTES = 8 * 1024 * 1024
DEFAULT_HEIGHT = 300
MIN_HEIGHT = 200
MAX_HEIGHT = 380
DEFAULT_FOCUS = "center"
FOCUS_CHOICES = (
    "top-left",
    "top",
    "top-right",
    "left",
    "center",
    "right",
    "bottom-left",
    "bottom",
    "bottom-right",
)

# 图片格式魔数 sniffing：(偏移, 魔数, 扩展名)
_MAGIC: tuple[tuple[int, bytes, str], ...] = (
    (0, b"\x89PNG\r\n\x1a\n", "png"),
    (0, b"\xff\xd8\xff", "jpg"),
    (0, b"GIF87a", "gif"),
    (0, b"GIF89a", "gif"),
    (0, b"RIFF", "webp"),  # RIFF....WEBP，偏移 8 处再验 WEBP
)


def sniff_image_ext(data: bytes) -> str | None:
    """按魔数识别图片格式，返回扩展名；不识别返回 None。"""
    for offset, magic, ext in _MAGIC:
        if data[offset : offset + len(magic)] == magic:
            if ext == "webp" and data[8:12] != b"WEBP":
                continue
            return ext
    return None


def _resolve(relative: str) -> Path:
    """把 ``covers/cover.png`` 这类相对路径解析成实际路径。"""
    path = Path(relative)
    return path if path.is_absolute() else DATA_DIR / path


def load_meta() -> dict[str, Any]:
    """读取封面元信息；没有封面或文件丢了都返回空字典。"""
    try:
        raw = META_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    relative = str(data.get("path") or "").strip()
    if not relative or not _resolve(relative).is_file():
        return {}
    focus = str(data.get("focus") or DEFAULT_FOCUS)
    try:
        height = int(data.get("height") or DEFAULT_HEIGHT)
    except (TypeError, ValueError):
        height = DEFAULT_HEIGHT
    return {
        "path": relative,
        "focus": focus if focus in FOCUS_CHOICES else DEFAULT_FOCUS,
        "height": max(MIN_HEIGHT, min(MAX_HEIGHT, height)),
        "updated_at": str(data.get("updated_at") or ""),
    }


def save_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """写入元信息并返回规范化后的结果。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "path": str(meta.get("path") or ""),
        "focus": str(meta.get("focus") or DEFAULT_FOCUS),
        "height": int(meta.get("height") or DEFAULT_HEIGHT),
        "updated_at": str(meta.get("updated_at") or _now()),
    }
    payload["focus"] = payload["focus"] if payload["focus"] in FOCUS_CHOICES else DEFAULT_FOCUS
    payload["height"] = max(MIN_HEIGHT, min(MAX_HEIGHT, payload["height"]))
    META_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def image_path() -> Path | None:
    """当前封面的实际路径；没有就返回 None。"""
    meta = load_meta()
    if not meta:
        return None
    path = _resolve(meta["path"])
    return path if path.is_file() else None


def save_upload(data: bytes) -> dict[str, Any]:
    """保存上传的封面图。

    Args:
        data: 原始图片字节。

    Returns:
        规范化后的元信息。

    Raises:
        ValueError: 太大或不是可识别的图片。
    """
    if not data:
        raise ValueError("没有收到图片数据")
    if len(data) > MAX_BYTES:
        raise ValueError(f"图片太大了（上限 {MAX_BYTES // (1024 * 1024)} MB）")
    ext = sniff_image_ext(data)
    if ext is None:
        raise ValueError("这不是能识别的图片格式（支持 PNG / JPEG / WebP / GIF）")

    COVER_DIR.mkdir(parents=True, exist_ok=True)
    image_name = f"cover.{ext}"
    target = COVER_DIR / image_name
    target.write_bytes(data)

    # 清掉旧格式留下的其他 cover.* 文件，避免历史文件堆积
    for old in COVER_DIR.glob("cover.*"):
        if old != target:
            try:
                old.unlink()
            except OSError:
                pass

    previous = load_meta()
    return save_meta(
        {
            "path": f"covers/{image_name}",
            "focus": previous.get("focus") or DEFAULT_FOCUS,
            "height": previous.get("height") or DEFAULT_HEIGHT,
            "updated_at": _now(),
        }
    )


def update(focus: str | None = None, height: int | None = None) -> dict[str, Any]:
    """只改焦点/高度，不换图。"""
    meta = load_meta()
    if not meta:
        raise ValueError("还没有上传封面")
    if focus is not None:
        meta["focus"] = str(focus)
    if height is not None:
        meta["height"] = height
    meta["updated_at"] = _now()
    return save_meta(meta)


def clear() -> None:
    """删除封面与元信息。"""
    path = image_path()
    if path is not None:
        try:
            path.unlink()
        except OSError:
            pass
    try:
        META_FILE.unlink()
    except OSError:
        pass


def focus_to_css(focus: str) -> str:
    """把焦点名映射成 CSS ``object-position``。"""
    mapping = {
        "top-left": "0% 0%",
        "top": "50% 0%",
        "top-right": "100% 0%",
        "left": "0% 50%",
        "center": "50% 50%",
        "right": "100% 50%",
        "bottom-left": "0% 100%",
        "bottom": "50% 100%",
        "bottom-right": "100% 100%",
    }
    return mapping.get(focus, "50% 50%")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
