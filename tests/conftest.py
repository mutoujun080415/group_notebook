"""测试公共配置：包路径 + 框架 ``src`` 的轻量替身。

插件代码里 ``collector/analyzer/models/reports/renderer`` 都不依赖框架，
只有 ``config/webui_standalone`` 等模块在导入期需要 ``src.*``。
本机没有 Neo-MoFox 源码时（例如独立开发 venv），注入最小替身即可跑测试；
在 bot 环境里运行时（能找到真实 ``src``），一律用真框架。
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import sys
import types as _types
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = PLUGIN_DIR.parent

if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))


def _has_real_src() -> bool:
    try:
        return importlib.util.find_spec("src") is not None
    except (ImportError, ValueError):
        return False


def _install_src_stub() -> None:
    """构造最小可用的 src.app.plugin_system 替身。"""
    import argparse  # noqa: F401  (占位，保持 import 风格一致)

    def module(name: str) -> _types.ModuleType:
        mod = _types.ModuleType(name)
        sys.modules[name] = mod
        return mod

    src = module("src")
    app = module("src.app")
    plugin_system = module("src.app.plugin_system")
    api_pkg = module("src.app.plugin_system.api")
    base_pkg = module("src.app.plugin_system.base")
    types_mod = module("src.app.plugin_system.types")
    log_api = module("src.app.plugin_system.api.log_api")

    src.app = app
    app.plugin_system = plugin_system
    plugin_system.api = api_pkg
    plugin_system.base = base_pkg
    plugin_system.types = types_mod
    api_pkg.log_api = log_api

    log_api.get_logger = lambda name, **kwargs: logging.getLogger(name)

    # ---- types: ChatStream / PermissionLevel / EventType ----
    class ChatStream:  # noqa: D401
        """与框架同名的最小替身：只提供 stream_id 生成。"""

        @staticmethod
        def generate_stream_id(platform: str, group_id: str = "", user_id: str = "",
                               chat_type: str = "") -> str:
            raw = ":".join(part for part in (platform, chat_type, group_id, user_id) if part)
            return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    class PermissionLevel:  # noqa: D401
        GUEST = "guest"
        USER = "user"
        OPERATOR = "operator"
        OWNER = "owner"

    class EventType:  # noqa: D401
        CUSTOM = "custom"

    types_mod.ChatStream = ChatStream
    types_mod.PermissionLevel = PermissionLevel
    types_mod.EventType = EventType

    # ---- base: 组件基类与配置工具 ----
    class _ComponentBase:
        def __init__(self, plugin=None, *args, **kwargs):
            self.plugin = plugin

    class BasePlugin(_ComponentBase):
        plugin_name = ""
        configs: list = []

        def __init__(self, config=None):
            self.config = config

    class BaseService(_ComponentBase):
        pass

    class BaseCommand(_ComponentBase):
        def __init__(self, plugin=None, stream_id="", message_id="", message=None):
            super().__init__(plugin)
            self.stream_id = stream_id
            self.message_id = message_id
            self.message = message

    class BaseRouter(_ComponentBase):
        name = ""
        custom_route_path = ""
        cors_origins: list = []

        def __init__(self, plugin=None):
            self.plugin = plugin
            self.app = None

        def get_route_path(self) -> str:
            return self.custom_route_path

        def register_endpoints(self) -> None:  # pragma: no cover
            raise NotImplementedError

    def register_plugin(cls):  # noqa: D401
        return cls

    def cmd_route(*path):  # noqa: D401
        def decorator(func):
            func._cmd_route = list(path)
            return func
        return decorator

    # ---- 配置系统替身：Field/config_section/SectionBase/BaseConfig ----
    class Field:
        def __init__(self, default=None, default_factory=None, description="", label=""):
            self.default = default
            self.default_factory = default_factory
            self.description = description
            self.label = label

        def build(self):
            if self.default_factory is not None:
                return self.default_factory()
            return self.default

    class SectionBase:
        def __init__(self):
            for key in type(self).__dict__:
                if key.startswith("_"):
                    continue
                value = getattr(type(self), key)
                if isinstance(value, Field):
                    setattr(self, key, value.build())

    def config_section(*_args, **_kwargs):
        def decorator(cls):
            return cls
        return decorator

    class BaseConfig(SectionBase):
        def __init__(self):
            for key in type(self).__dict__:
                if key.startswith("_"):
                    continue
                value = getattr(type(self), key)
                if isinstance(value, Field):
                    instance = value.build()
                    if isinstance(instance, SectionBase):
                        pass  # SectionBase.__init__ 已在 build 时完成字段展开
                    setattr(self, key, instance)

    base_pkg.BasePlugin = BasePlugin
    base_pkg.BaseService = BaseService
    base_pkg.BaseCommand = BaseCommand
    base_pkg.BaseRouter = BaseRouter
    base_pkg.register_plugin = register_plugin
    base_pkg.cmd_route = cmd_route
    base_pkg.Field = Field
    base_pkg.SectionBase = SectionBase
    base_pkg.config_section = config_section
    base_pkg.BaseConfig = BaseConfig


if not _has_real_src():
    _install_src_stub()
