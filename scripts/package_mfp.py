"""把插件打包成 .mfp（zip deflate，清单在包根），与 Neo-MoFox 加载器兼容。

用法::

    python scripts/package_mfp.py [输出目录]

输出 ``group_notebook-{version}.mfp``（版本号取自 manifest.json）。
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# 打包排除：开发产物与测试不进包，保持包体干净
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", "demo_out"}
EXCLUDE_FILES = {".coverage"}


def build_manifest() -> dict:
    """读取并返回 manifest.json。"""
    return json.loads((PLUGIN_ROOT / "manifest.json").read_text(encoding="utf-8"))


def collect_files() -> list[Path]:
    """收集需要打包的文件（相对插件根）。"""
    files: list[Path] = []
    for path in sorted(PLUGIN_ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(PLUGIN_ROOT)
        parts = relative.parts
        if any(part in EXCLUDE_DIRS for part in parts):
            continue
        if any(part.startswith(".") for part in parts[:-1]):
            continue
        if relative.name in EXCLUDE_FILES or relative.name.endswith(".pyc"):
            continue
        if relative.suffix in {".mfp"}:
            continue
        files.append(relative)
    return files


def package(output_dir: Path) -> Path:
    """打包插件，返回 .mfp 路径。"""
    manifest = build_manifest()
    name = manifest["name"]
    version = manifest["version"]
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{name}-{version}.mfp"

    files = collect_files()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for relative in files:
            zf.write(PLUGIN_ROOT / relative, arcname=str(relative))
    size = target.stat().st_size
    print(f"OK: {target}（{size / 1048576:.1f} MB，{len(files)} 个文件）")
    return target


def main() -> int:
    output_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else PLUGIN_ROOT / "dist"
    package(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
