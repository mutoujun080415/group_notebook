"""打包 Neo-MoFox 插件为 .mfp（白名单模式，只含运行必需文件）。

用法::

    python scripts/package_mfp.py [输出目录]

产物结构对照 Neo-MoFox 插件开发规范（manifest.json 在包根，
entry_point 指向 plugin.py），不含测试/脚本/开发产物。
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# ── 白名单：.mfp 里只包含这些文件 ──
# 与 Neo-MoFox 插件开发规范一致：manifest + 源码 + 模板 + WebUI + 运行时资产
INCLUDE_FILES = [
    # 必需
    "manifest.json",
    "__init__.py",
    "plugin.py",
    "config.py",
    "command.py",
    "service.py",
    "collector.py",
    "analyzer.py",
    "dossier_analyzer.py",
    "models.py",
    "renderer.py",
    "pillow_renderer.py",
    "pillow_dossier.py",
    "reports.py",
    "cover.py",
    "router.py",
    "webui_standalone.py",
    # 模板
    "templates/notebook.html",
    "templates/dossier.html",
    # WebUI 页面
    "webui/index.html",
    # 运行时资产（字体）
    "assets/fonts/LXGWWenKai-Medium.ttf",
    "assets/fonts/LXGWWenKai-Regular.ttf",
    "assets/fonts/ZCOOLKuaiLe-Regular.ttf",
    # 市场/文档展示
    "README.md",
    "LICENSE",
    "assets/preview.png",
]


def build_manifest() -> dict:
    """读取并返回 manifest.json。"""
    return json.loads((PLUGIN_ROOT / "manifest.json").read_text(encoding="utf-8"))


def package(output_dir: Path) -> Path:
    """打包插件，返回 .mfp 路径。"""
    manifest = build_manifest()
    name = manifest["name"]
    version = manifest["version"]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{name}-{version}.mfp"

    # 校验白名单文件全部存在
    missing = [f for f in INCLUDE_FILES if not (PLUGIN_ROOT / f).is_file()]
    if missing:
        raise FileNotFoundError(f"白名单文件缺失：{missing}")

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for relative in INCLUDE_FILES:
            zf.write(PLUGIN_ROOT / relative, arcname=relative)

    size = target.stat().st_size
    file_count = len(INCLUDE_FILES)
    print(f"OK: {target}（{size / 1048576:.1f} MB，{file_count} 个文件）")
    return target


def main() -> int:
    output_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else PLUGIN_ROOT / "dist"
    package(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
