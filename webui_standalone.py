"""群聊摘录独立 WebUI（自挂子应用）。

不依赖官方 Plugin UI：页面和接口都由这个子应用自己提供，路径由
``[webui].route_path`` 决定（默认 ``/plugins/group-notebook``）。
页面是单文件 ``webui/index.html``，接口统一在子应用的 ``/api/*`` 下，
只监听本机、无需 API Key。
"""

from __future__ import annotations

import hashlib
from datetime import date as date_cls
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.types import ChatStream

from . import cover
from .collector import (
    fetch_range_messages,
    member_bot_stats,
    member_target_stats,
    monday_of,
    range_days,
    split_bot_messages,
    today_in_timezone,
)
from .reports import build_stem, delete_report_files
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


class BatchPayload(BaseModel):
    """批量生成请求体。

    注意：必须定义在模块层——本文件开了 ``from __future__ import annotations``，
    函数局部类的字符串注解无法被 FastAPI 解析，会被当成 query 参数导致 422。
    """

    group_id: str = Field(default="", description="群号，留空用第一个配置群")
    date_start: str = Field(default="", description="起始日期 YYYY-MM-DD")
    date_end: str = Field(default="", description="结束日期 YYYY-MM-DD（含）")
    regenerate: bool = Field(default=False, description="已有图也重新生成")


class DeletePayload(BaseModel):
    """删除请求体。"""

    slug: str = Field(default="", description="聊天流 slug")
    stem: str = Field(default="", description="产物文件名 stem")


class DossierPayload(BaseModel):
    """案底生成请求体。"""

    group_id: str = Field(default="", description="群号，留空用第一个配置群")
    date: str = Field(default="", description="起始日期 YYYY-MM-DD，留空为今天")
    date_end: str = Field(default="", description="截止日期 YYYY-MM-DD；留空与起始同日")
    direction: str = Field(default="", description="member_as_culprit（成员迫害其他成员）/ member_as_victim（成员被围攻），留空用配置默认")
    members: list[str] = Field(default_factory=list, description="聚焦成员 QQ 号列表，留空自动 Top N")
    send: bool = Field(default=False, description="生成后是否发群")
    regenerate: bool = Field(default=False, description="已有图也重新生成")


class WeekPayload(BaseModel):
    """周记生成请求体。"""

    group_id: str = Field(default="", description="群号，留空用第一个配置群")
    date: str = Field(default="", description="起始日期 YYYY-MM-DD，留空为本周")
    date_end: str = Field(default="", description="截止日期 YYYY-MM-DD；留空为自然周")
    send: bool = Field(default=False, description="生成后是否发群")
    regenerate: bool = Field(default=False, description="已有图也重新生成")


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
                "render_engine": cfg.notebook.render_engine,
                "weekly_auto_send": cfg.general.weekly_auto_send,
                "weekly_send_day": cfg.general.weekly_send_day,
                "weekly_send_time": cfg.general.weekly_send_time,
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
    async def get_image(slug: str, stem: str) -> FileResponse:
        """返回摘录 PNG（stem 为产物文件名，如 week-2026-11-02_2026-11-08）。"""
        svc = service()
        if svc is None:
            raise HTTPException(status_code=503, detail="摘录服务尚未初始化")
        path = report_file(svc.data_dir(), slug, stem)
        if path is None:
            raise HTTPException(status_code=404, detail="摘录不存在")
        return FileResponse(
            path=str(path),
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=60"},
        )

    @app.get("/api/cover")
    async def get_cover() -> dict[str, Any]:
        """当前封面状态（没有也返回 200，页面好渲染空状态）。"""
        meta = cover.load_meta()
        return _ok(
            {
                "exists": bool(meta),
                "focus": meta.get("focus") or cover.DEFAULT_FOCUS,
                "height": int(meta.get("height") or cover.DEFAULT_HEIGHT),
                "updated_at": str(meta.get("updated_at") or ""),
                "url": "/api/cover/image" if meta else "",
                "focusChoices": list(cover.FOCUS_CHOICES),
                "minHeight": cover.MIN_HEIGHT,
                "maxHeight": cover.MAX_HEIGHT,
                "maxBytes": cover.MAX_BYTES,
            }
        )

    @app.get("/api/cover/image")
    async def get_cover_image() -> FileResponse:
        """封面原图，用于页面里的预览。"""
        path = cover.image_path()
        if path is None:
            raise HTTPException(status_code=404, detail="还没有封面")
        media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return FileResponse(
            path=str(path),
            media_type=media_type,
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/cover")
    async def upload_cover(request: Request) -> dict[str, Any]:
        """上传封面：请求体就是图片字节（不做 multipart，少一个依赖）。"""
        data = await request.body()
        try:
            meta = cover.save_upload(data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        logger.info(f"封面已更新：{meta['path']}（焦点 {meta['focus']}，高度 {meta['height']}px）")
        return _ok(meta, message="封面已更新，下一条摘录生效")

    @app.put("/api/cover")
    async def update_cover(payload: dict[str, Any]) -> dict[str, Any]:
        """只调焦点/高度，不重新上传。"""
        try:
            meta = cover.update(
                focus=payload.get("focus"),
                height=payload.get("height"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _ok(meta, message="封面参数已更新")

    @app.delete("/api/cover")
    async def delete_cover() -> dict[str, Any]:
        """删除封面，海报回到配置的立绘/默认页眉。"""
        cover.clear()
        return _ok({"exists": False}, message="封面已删除")

    def _resolve_group(payload_group: str) -> tuple[dict[str, str], str]:
        rows = group_rows()
        if not rows:
            raise HTTPException(status_code=400, detail="还没有配置群号，先在插件配置里填 general.groups")
        group_id = payload_group.strip() or rows[0]["group_id"]
        row = next((item for item in rows if item["group_id"] == group_id), None)
        if row is None:
            raise HTTPException(status_code=400, detail=f"群 {group_id} 不在 general.groups 中")
        return row, group_id

    @app.get("/api/members")
    async def get_members(group_id: str = "", date: str = "") -> dict[str, Any]:
        """返回指定周与 bot 有互动的成员清单（用于案底聚焦选择）。"""
        cfg = config()
        svc = service()
        if svc is None:
            raise HTTPException(status_code=503, detail="摘录服务尚未初始化")
        rows = group_rows()
        if not rows:
            raise HTTPException(status_code=400, detail="还没有配置群号")
        group = group_id.strip() or rows[0]["group_id"]
        row = next((item for item in rows if item["group_id"] == group), None)
        if row is None:
            raise HTTPException(status_code=400, detail=f"群 {group} 不在 general.groups 中")
        anchor = date.strip() or today_in_timezone(cfg.general.timezone)
        if not is_valid_day(anchor):
            raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD")
        monday = monday_of(anchor)
        stream_id = ChatStream.generate_stream_id(cfg.general.platform, group_id=group)
        messages = await fetch_range_messages(
            stream_id, monday, 7, cfg.general.timezone, include_bot=True, max_messages=0,
        )
        human, bot_messages = await split_bot_messages(messages)
        bot_ids = {
            str(message.get("sender_id") or "")
            for message in bot_messages if str(message.get("sender_id") or "")
        }
        out_stats = member_bot_stats(
            human + bot_messages, bot_ids=bot_ids,
            bot_name=cfg.notebook.bot_name,
        )
        target_stats = member_target_stats(human + bot_messages)
        all_ids = set(out_stats) | set(target_stats)
        members = []
        for sender in all_ids:
            name = (out_stats.get(sender) or target_stats.get(sender) or {}).get("name", sender)
            members.append({
                "qq": sender,
                "name": name,
                "out_count": out_stats.get(sender, {}).get("count", 0),
                "in_count": target_stats.get(sender, {}).get("count", 0),
            })
        members.sort(key=lambda item: max(item["out_count"], item["in_count"]),
                     reverse=True)
        return _ok({
            "week": monday,
            "members": members[:30],
        })

    @app.post("/api/delete")
    async def delete_record(payload: DeletePayload) -> dict[str, Any]:
        """删除一条已生成的摘录记录（PNG/JSON/中间 HTML）。"""
        svc = service()
        if svc is None:
            raise HTTPException(status_code=503, detail="摘录服务尚未初始化")
        from .reports import delete_report_files

        deleted = delete_report_files(svc.data_dir(), payload.slug, payload.stem)
        if not deleted:
            raise HTTPException(status_code=404, detail="记录不存在或已删除")
        logger.info(f"摘录记录已删除：{payload.slug}/{payload.stem}")
        return _ok({"deleted": True, "slug": payload.slug, "stem": payload.stem},
                   message="记录已删除")

    @app.post("/api/generate_dossier")
    async def generate_dossier_endpoint(payload: DossierPayload) -> dict[str, Any]:
        """生成（可选发送）指定周的专题案底卷宗。"""
        cfg = config()
        svc = service()
        if svc is None:
            raise HTTPException(status_code=503, detail="摘录服务尚未初始化")
        if not cfg.dossier.enabled:
            raise HTTPException(status_code=400, detail="案底功能已在配置中关闭（dossier.enabled）")
        row, group_id = _resolve_group(payload.group_id)
        anchor = payload.date.strip() or today_in_timezone(cfg.general.timezone)
        if not is_valid_day(anchor):
            raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD")
        today = today_in_timezone(cfg.general.timezone)
        if anchor > today:
            raise HTTPException(status_code=400, detail="不能生成未来日期的案底")
        monday = monday_of(anchor)
        sunday = (date_cls.fromisoformat(monday) + timedelta(days=6)).isoformat()

        stream_id = ChatStream.generate_stream_id(cfg.general.platform, group_id=group_id)
        try:
            if payload.send:
                ok, message = await svc.send_dossier(
                    stream_id, monday, end_day=payload.date_end,
                    room_name=row["name"], members=payload.members or None,
                    direction=payload.direction, regenerate=payload.regenerate,
                )
            else:
                ok, message, _path = await svc.generate_dossier(
                    stream_id, monday, end_day=payload.date_end,
                    room_name=row["name"], members=payload.members or None,
                    direction=payload.direction, force=payload.regenerate,
                )
        except Exception as exc:
            logger.error(f"独立页面生成案底失败：group={group_id} week={monday} {exc}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"案底生成失败：{exc}") from exc
        dossier_stem = build_stem(
            "dossier", monday, sunday,
            key=hashlib.md5(
                f"{payload.direction or 'default'}|{','.join(sorted(payload.members)) or 'auto'}".encode("utf-8")
            ).hexdigest()[:8],
        )
        return _ok(
            {
                "ok": bool(ok),
                "message": message,
                "week": monday,
                "group_id": group_id,
                "url": report_url(row["slug"], dossier_stem),
            },
            message=message,
        )

    @app.post("/api/generate_batch")
    async def generate_batch(payload: BatchPayload) -> dict[str, Any]:
        """批量生成日期区间内的逐日摘录（只生成不发送，最多 31 天）。"""
        cfg = config()
        svc = service()
        if svc is None:
            raise HTTPException(status_code=503, detail="摘录服务尚未初始化")
        row, group_id = _resolve_group(payload.group_id)

        today = today_in_timezone(cfg.general.timezone)
        start = payload.date_start.strip() or today
        end = payload.date_end.strip() or start
        if not is_valid_day(start) or not is_valid_day(end):
            raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD")
        days = range_days(start, end)
        if not days:
            raise HTTPException(status_code=400, detail="结束日期早于起始日期")
        days = [day for day in days if day <= today]
        if not days:
            raise HTTPException(status_code=400, detail="区间内没有可生成的日期")
        days = days[:31]

        stream_id = ChatStream.generate_stream_id(cfg.general.platform, group_id=group_id)
        results = []
        ok_count = 0
        for day in days:
            try:
                ok, message, _path = await svc.generate(
                    stream_id, day, room_name=row["name"],
                    force=payload.regenerate,
                )
            except Exception as exc:
                logger.error(f"批量生成失败：group={group_id} day={day} {exc}", exc_info=True)
                ok, message = False, f"生成失败：{exc}"
            results.append({"date": day, "ok": bool(ok), "message": message})
            if ok:
                ok_count += 1
        generated = sum(1 for item in results if item["ok"])
        return _ok(
            {
                "group_id": group_id,
                "total": len(results),
                "ok": ok_count,
                "results": results,
            },
            message=f"批量完成：成功生成/复用 {ok_count}/{len(results)} 天",
        )

    @app.post("/api/generate_week")
    async def generate_week_endpoint(payload: WeekPayload) -> dict[str, Any]:
        """生成（可选发送）指定日期所在自然周的手账周记。"""
        cfg = config()
        svc = service()
        if svc is None:
            raise HTTPException(status_code=503, detail="摘录服务尚未初始化")
        row, group_id = _resolve_group(payload.group_id)

        anchor = payload.date.strip() or today_in_timezone(cfg.general.timezone)
        if not is_valid_day(anchor):
            raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD")
        today = today_in_timezone(cfg.general.timezone)
        if anchor > today:
            raise HTTPException(status_code=400, detail="不能生成未来日期的周记")
        monday = monday_of(anchor)
        sunday = (date_cls.fromisoformat(monday) + timedelta(days=6)).isoformat()

        stream_id = ChatStream.generate_stream_id(cfg.general.platform, group_id=group_id)
        try:
            if payload.send:
                ok, message = await svc.send_week(
                    stream_id, monday, end_day=payload.date_end,
                    room_name=row["name"], regenerate=payload.regenerate,
                )
            else:
                ok, message, _path = await svc.generate_week(
                    stream_id, monday, end_day=payload.date_end,
                    room_name=row["name"], force=payload.regenerate,
                )
        except Exception as exc:
            logger.error(f"独立页面生成周记失败：group={group_id} {start_hint} {exc}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"周记生成失败：{exc}") from exc

        week_stem = build_stem("week", monday, sunday)
        return _ok(
            {
                "ok": bool(ok),
                "message": message,
                "week": monday,
                "group_id": group_id,
                "url": report_url(row["slug"], week_stem),
            },
            message=message,
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
