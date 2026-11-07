"""群聊摘录独立页面的 Router。

把 ``webui_standalone.create_app()`` 生成的子应用挂到主程序 HTTP 服务器的
``[webui].route_path`` 上（默认 ``/plugins/group-notebook``），不经过官方
Plugin UI 插件，所以 ``.mfp`` 安装也能用页面。
"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseRouter

from .config import GroupNotebookConfig
from .webui_standalone import create_app, webui_full_url

logger = get_logger("group_notebook")

DEFAULT_ROUTE_PATH = "/plugins/group-notebook"


class NotebookWebUIRouter(BaseRouter):
    """群聊摘录独立 WebUI（自挂子应用）。"""

    name = "notebook_webui"
    description = "群聊摘录独立页面：选群选日期、生成手账、查看历史"
    dependencies: list[str] = []
    cors_origins = ["*"]

    def __init__(self, plugin: Any) -> None:
        """从插件配置里读出挂载路径。"""
        config = getattr(plugin, "config", None)
        if isinstance(config, GroupNotebookConfig):
            route_path = (config.webui.route_path or "").strip()
            self.custom_route_path = route_path or DEFAULT_ROUTE_PATH
        self._sub_app: Any | None = None
        super().__init__(plugin)

    def register_endpoints(self) -> None:
        """挂载子应用。"""
        self._sub_app = create_app(self.plugin)
        self.app.mount("/", self._sub_app)

    async def startup(self) -> None:
        """启动钩子：解析主程序真实端口并打印完整访问地址。"""
        route_path = self.get_route_path()
        full_url = webui_full_url(route_path)
        if full_url:
            logger.info(f"群聊摘录 WebUI 已挂载：{full_url}（地址/端口跟随 core.toml 的 [http_router]）")
        else:
            logger.warning(
                f"群聊摘录 WebUI 已挂载到 {route_path}，但主程序 HTTP 路由未启用或尚未就绪，"
                "暂无法访问；请检查 core.toml 的 [http_router]（enable_http_router / http_router_port）"
            )

    async def shutdown(self) -> None:
        """关闭钩子。"""
        self._sub_app = None
