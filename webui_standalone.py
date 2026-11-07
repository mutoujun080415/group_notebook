"""群聊摘录独立 WebUI（自挂子应用）。

不依赖官方 Plugin UI：页面和接口都由这个子应用自己提供，路径由
``[webui].route_path`` 决定（默认 ``/plugins/group-notebook``）。
页面是单文件 ``webui/index.html``，接口统一在子应用的 ``/api/*`` 下，
只监听本机、无需 API Key。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.types import ChatStream

from .collector import today_in_timezone
from .config import GroupNotebookConfig
from .reports import is_valid_day, report_file, report_url, slug_for_stream

logger = get_logger("group_notebook")

PAGE_FILE = Path(__file__).with_name("webui") / "index.html"
NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}


def _display_host(host: str | None) -> str:
    """把监听地址转成适合展示/访问的主机名（全零地址映射回 127.0.0.1）。"""
    text = str(host or "").strip() or "127.0.0.1"
    if text in {"0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return text


def resolve_http_base_url() -> str:
    """解析主程序 HTTP 服务器的对外基础地址（跟随运行时与 core.toml）。

    返回形如 ``http://127.0.0.1:8010`` 的基础地址（端口以运行时/配置为准）；
    HTTP 路由被禁用或无法
    确定时返回空串，由调用方决定如何提示。

    注意：这里**绝不**调用 ``get_http_server()``——它会在单例缺失时用默认
    参数（8000 端口）提前创建实例，而框架是先按 core.toml 建好服务器再加载
    插件的，提前创建会把真实端口污染成默认值。因此只读已存在的单例。
    """
    # 1) 框架已创建的 HTTP 服务器单例（只读访问，不触发创建）
    try:
        from src.core.transport.router import http_server as _http_server_module

        server = getattr(_http_server_module, "_global_http_server", None)
        if server is not None:
            port = int(getattr(server, "port", 0) or 0)
            if port > 0:
                return f"http://{_display_host(getattr(server, 'host', None))}:{port}"
    except Exception:
        pass
    # 2) 核心配置的 [http_router] 节（core.toml 可改 host/port）
    try:
        from src.app.plugin_system.api import config_api

        section = getattr(config_api.get_core_config(), "http_router", None)
        if section is not None and not bool(getattr(section, "enable_http_router", True)):
            return ""
        port = int(getattr(section, "http_router_port", 0) or 0)
        if port > 0:
            host = _display_host(getattr(section, "http_router_host", None))
            return f"http://{host}:{port}"
    except Exception:
        pass
    # 3) 无法确定（含离线测试环境）：返回空串，不猜端口
    return ""


def webui_full_url(route_path: str) -> str:
    """返回独立页面的完整地址；HTTP 路由不可用时返回空串。"""
    base = resolve_http_base_url()
    if not base:
        return ""
    return f"{base}{(route_path or '').rstrip('/')}/"


class GeneratePayload(BaseModel):
    """生成请求体。"""

    group_id: str = Field(default="", description="群号，留空用第一个配置群")
    date: str = Field(default="", description="YYYY-MM-DD，留空表示今天")
    send: bool = Field(default=False, description="生成后是否直接发群")
    regenerate: bool = Field(default=False, description="忽略已有图片重新生成")


def _ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    """统一响应形状。"""
    return {"code": 200, "message": message, "data": data}


def create_app(plugin: Any) -> FastAPI:
    """按插件实例创建一个只读 + 一次生成的子应用。"""
    app = FastAPI(
        title="群聊摘录 WebUI",
        description="群聊摘录的独立页面：选群、选日期、生成手账、补发",
        docs_url=None,
        redoc_url=None,
    )
    app.state.plugin = plugin

    def config() -> GroupNotebookConfig:
        raw = getattr(plugin, "config", None)
        return raw if isinstance(raw, GroupNotebookConfig) else GroupNotebookConfig()

    def service() -> Any:
        return getattr(plugin, "notebook", None)

    def group_rows() -> list[dict[str, str]]:
        cfg = config()
        svc = service()
        aliases = svc.group_room_names() if svc is not None else {}
        rows: list[dict[str, str]] = []
        for raw in cfg.general.groups:
            group_id = str(raw or "").strip()
            if not group_id:
                continue
            stream_id = ChatStream.generate_stream_id(cfg.general.platform, group_id=group_id)
            rows.append(
                {
                    "group_id": group_id,
                    "name": aliases.get(group_id, cfg.notebook.room_name),
                    "slug": slug_for_stream(stream_id),
                }
            )
        return rows

    @app.get("/")
    async def index() -> FileResponse:
        """页面本体。"""
        if not PAGE_FILE.is_file():
            raise HTTPException(status_code=500, detail="webui/index.html 缺失")
        return FileResponse(PAGE_FILE, media_type="text/html; charset=utf-8", headers=NO_CACHE)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """健康检查。"""
        return {"status": "ok", "service": "group_notebook_webui"}

    @app.get("/api/state")
    async def get_state() -> dict[str, Any]:
        """群列表、已生成数、最近一期、WebUI 完整地址（端口跟随主程序配置）。"""
        cfg = config()
        svc = service()
        reports = svc.list_reports(limit=0) if svc is not None else []
        latest = reports[0].to_dict() if reports else None
        route_path = (cfg.webui.route_path or "").strip() or "/plugins/group-notebook"
        base_url = resolve_http_base_url()
        from .reports import report_url as _report_url

        return _ok(
            {
                "version": str(getattr(plugin, "plugin_version", "") or "0.1.0"),
                "groups": group_rows(),
                "reports": len(reports),
                "latest": latest,
                "limit": cfg.webui.default_limit,
                "mounted": True,
                "keep_days": cfg.storage.keep_days,
                "route_path": route_path,
                "http_base_url": base_url,
                "webui_url": f"{base_url}{route_path.rstrip('/')}/" if base_url else "",
                "latest_url": _report_url(latest["slug"], latest["date"], route_path) if latest else "",
            }
        )

    @app.get("/api/reports")
    async def list_reports_endpoint(limit: int = 0) -> dict[str, Any]:
        """历史摘录，按日期倒序。"""
        cfg = config()
        svc = service()
        if svc is None:
            raise HTTPException(status_code=503, detail="摘录服务尚未初始化")
        maximum = limit if limit > 0 else cfg.webui.default_limit
        maximum = max(1, min(maximum, 200))
        name_by_slug = {row["slug"]: row["name"] for row in group_rows()}
        data = []
        for entry in svc.list_reports(limit=maximum):
            item = entry.to_dict()
            item["group_name"] = name_by_slug.get(entry.slug, "未知群")
            data.append(item)
        return _ok(data)

    @app.get("/api/image")
    async def get_image(slug: str, date: str) -> FileResponse:
        """返回摘录 PNG。"""
        svc = service()
        if svc is None:
            raise HTTPException(status_code=503, detail="摘录服务尚未初始化")
        path = report_file(svc.data_dir(), slug, date)
        if path is None:
            raise HTTPException(status_code=404, detail="摘录不存在")
        return FileResponse(
            path=str(path),
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=60"},
        )

    @app.post("/api/generate")
    async def generate(payload: GeneratePayload) -> dict[str, Any]:
        """生成（可选发送）指定群、指定日期的摘录。"""
        cfg = config()
        svc = service()
        if svc is None:
            raise HTTPException(status_code=503, detail="摘录服务尚未初始化")
        rows = group_rows()
        if not rows:
            raise HTTPException(status_code=400, detail="还没有配置群号，先在插件配置里填 general.groups")

        group_id = payload.group_id.strip() or rows[0]["group_id"]
        row = next((item for item in rows if item["group_id"] == group_id), None)
        if row is None:
            raise HTTPException(status_code=400, detail=f"群 {group_id} 不在 general.groups 中")

        day = payload.date.strip() or today_in_timezone(cfg.general.timezone)
        if not is_valid_day(day):
            raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD")
        if day > today_in_timezone(cfg.general.timezone):
            raise HTTPException(status_code=400, detail="不能生成未来日期的摘录")

        stream_id = ChatStream.generate_stream_id(cfg.general.platform, group_id=group_id)
        try:
            if payload.send:
                ok, message = await svc.send(
                    stream_id,
                    day,
                    room_name=row["name"],
                    regenerate=payload.regenerate,
                )
            else:
                ok, message, _path = await svc.generate(
                    stream_id,
                    day,
                    room_name=row["name"],
                    force=payload.regenerate,
                )
        except Exception as exc:  # 渲染/发送异常都要变成可读的 500
            logger.error(f"独立页面生成摘录失败：group={group_id} day={day} {exc}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"生成失败：{exc}") from exc

        return _ok(
            {
                "ok": bool(ok),
                "message": message,
                "date": day,
                "group_id": group_id,
                "url": report_url(row["slug"], day),
            },
            message=message,
        )

    return app
