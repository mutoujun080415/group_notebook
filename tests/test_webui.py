"""webui_standalone.py 的接口测试（假插件 + TestClient，不启动机器人）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from group_notebook.config import GroupNotebookConfig
from group_notebook.webui_standalone import create_app

pytest.importorskip("fastapi")
pytest.importorskip("httpx")


class _FakeService:
    """最小 Service 替身：只提供 WebUI 用到的只读方法。"""

    def group_room_names(self):
        return {"123456": "测试群"}

    def list_reports(self, limit: int = 60):
        from group_notebook.reports import ReportEntry, slug_for_stream
        from src.app.plugin_system.types import ChatStream

        slug = slug_for_stream(ChatStream.generate_stream_id("qq", group_id="123456"))
        return [ReportEntry(slug=slug, day="2026-11-05", size=1234, mtime=1793987400.0)][:limit]

    def data_dir(self):
        from pathlib import Path

        return Path("data") / "group_notebook"


def _plugin(notebook_service=None):
    config = GroupNotebookConfig()
    config.general.groups = ["123456"]
    return SimpleNamespace(
        config=config,
        notebook=notebook_service,
        plugin_version="0.1.0",
    )


@pytest.fixture()
def client():
    app = create_app(_plugin(notebook_service=_FakeService()))
    return TestClient(app)


def test_health(client):
    data = client.get("/api/health").json()
    assert data == {"status": "ok", "service": "group_notebook_webui"}


def test_state_contains_groups(client):
    payload = client.get("/api/state").json()
    assert payload["code"] == 200
    groups = payload["data"]["groups"]
    assert groups and groups[0]["group_id"] == "123456"
    assert groups[0]["name"] == "测试群"
    assert payload["data"]["version"] == "0.1.0"


def test_reports_mapped_names(client):
    payload = client.get("/api/reports").json()
    data = payload["data"]
    assert data[0]["group_name"] == "测试群"
    assert data[0]["url"].startswith("/plugins/group-notebook/api/image")


def test_image_missing_returns_404(client):
    response = client.get("/api/image", params={"slug": "abc", "date": "2026-11-05"})
    assert response.status_code == 404


def test_generate_without_service_returns_503():
    app = create_app(_plugin(notebook_service=None))
    client = TestClient(app)
    response = client.post(
        "/api/generate", json={"group_id": "123456", "date": "2026-11-05"}
    )
    assert response.status_code == 503


def test_generate_rejects_unknown_group(client):
    response = client.post(
        "/api/generate",
        json={"group_id": "999999", "date": "2026-11-05", "send": False},
    )
    assert response.status_code == 400


def test_generate_rejects_future_date(client):
    response = client.post(
        "/api/generate",
        json={"group_id": "123456", "date": "2099-01-01", "send": False},
    )
    assert response.status_code == 400


def test_generate_rejects_bad_date(client):
    response = client.post(
        "/api/generate",
        json={"group_id": "123456", "date": "not-a-day", "send": False},
    )
    assert response.status_code == 400


def test_index_page_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "群聊摘录" in response.text


PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture()
def cover_tmp(tmp_path, monkeypatch):
    """把封面存储重定向到临时目录，避免测试污染工作目录。"""
    from group_notebook import cover

    monkeypatch.setattr(cover, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cover, "COVER_DIR", tmp_path / "covers")
    monkeypatch.setattr(cover, "META_FILE", tmp_path / "cover.json")
    return tmp_path


def test_cover_full_lifecycle(client, cover_tmp):
    # 初始为空
    assert client.get("/api/cover").json()["data"]["exists"] is False
    # 上传
    response = client.post("/api/cover", content=PNG_1PX)
    assert response.status_code == 200
    # 查询
    data = client.get("/api/cover").json()["data"]
    assert data["exists"] is True
    assert data["focus"] == "center"
    # 取图
    image = client.get("/api/cover/image")
    assert image.status_code == 200
    # 调参数
    updated = client.put("/api/cover", json={"focus": "top", "height": 340})
    assert updated.json()["data"]["focus"] == "top"
    assert updated.json()["data"]["height"] == 340
    # 删除
    assert client.delete("/api/cover").status_code == 200
    assert client.get("/api/cover").json()["data"]["exists"] is False
    assert client.get("/api/cover/image").status_code == 404


def test_cover_upload_rejects_garbage(client, cover_tmp):
    response = client.post("/api/cover", content=b"definitely not an image")
    assert response.status_code == 400


def test_cover_update_without_upload(client, cover_tmp):
    response = client.put("/api/cover", json={"focus": "top"})
    assert response.status_code == 400


def test_generate_week_body_parsed_as_model():
    """回归：payload 模型曾定义在函数内，配合 __future__ annotations 被当成
    query 参数，导致 422 "Field required"。周记端点必须接受 JSON body。"""
    app = create_app(_plugin(notebook_service=None))
    web_client = TestClient(app)
    response = web_client.post(
        "/api/generate_week",
        json={"group_id": "123456", "date": "2026-11-05", "send": False},
    )
    # 服务未初始化时应是 503（说明已经走到端点内部），而不是 422 校验错误
    assert response.status_code == 503
    assert "摘录服务尚未初始化" in response.text


def test_generate_week_rejects_bad_date(client, cover_tmp):
    """带服务时，坏日期应在调用服务前被 400 拒绝。"""
    response = client.post(
        "/api/generate_week",
        json={"group_id": "123456", "date": "not-a-day", "send": False},
    )
    assert response.status_code == 400


def test_generate_week_rejects_future_date(client, cover_tmp):
    response = client.post(
        "/api/generate_week",
        json={"group_id": "123456", "date": "2099-01-01", "send": False},
    )
    assert response.status_code == 400


def test_generate_batch_body_parsed_as_model():
    """批量端点同样必须接受 JSON body（同一回归）。"""
    app = create_app(_plugin(notebook_service=None))
    web_client = TestClient(app)
    response = web_client.post(
        "/api/generate_batch",
        json={"group_id": "123456", "date_start": "2026-11-01",
              "date_end": "2026-11-03"},
    )
    # 无服务 → 503（走到端点内部）；若是 422 则说明 body 又被当成 query 了
    assert response.status_code == 503


def test_state_exposes_dynamic_url_fields(client):
    """WebUI 地址必须由运行时解析，state 里提供完整地址与子路径字段。"""
    payload = client.get("/api/state").json()["data"]
    assert payload["route_path"] == "/plugins/group-notebook"
    assert "http_base_url" in payload
    assert "webui_url" in payload
    assert "latest_url" in payload


class TestResolveHttpBaseUrl:
    def test_display_host_mapping(self):
        from group_notebook.webui_standalone import _display_host

        assert _display_host("0.0.0.0") == "127.0.0.1"
        assert _display_host("::") == "127.0.0.1"
        assert _display_host("") == "127.0.0.1"
        assert _display_host("192.168.1.5") == "192.168.1.5"

    def test_offline_returns_empty_not_guess(self):
        """离线（无框架/无运行时服务器）时返回空串，绝不猜端口。"""
        from group_notebook.webui_standalone import resolve_http_base_url, webui_full_url

        assert resolve_http_base_url() == ""
        assert webui_full_url("/plugins/group-notebook") == ""

    def test_prefers_running_server_and_maps_zero_host(self, monkeypatch):
        """运行中的服务器单例优先，且 0.0.0.0 展示为 127.0.0.1。"""
        import sys
        import types

        for name in ("src.core", "src.core.transport", "src.core.transport.router"):
            if name not in sys.modules:
                monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
        fake_server_module = types.ModuleType("src.core.transport.router.http_server")
        fake_server_module._global_http_server = SimpleNamespace(host="0.0.0.0", port=9321)
        monkeypatch.setitem(sys.modules, "src.core.transport.router.http_server", fake_server_module)
        setattr(sys.modules["src.core.transport.router"], "http_server", fake_server_module)

        from group_notebook.webui_standalone import resolve_http_base_url

        assert resolve_http_base_url() == "http://127.0.0.1:9321"

    def test_disabled_router_returns_empty(self, monkeypatch):
        """core.toml 关闭 enable_http_router 时返回空串。"""
        import sys
        import types

        fake_config_api = types.ModuleType("src.app.plugin_system.api.config_api")
        fake_config_api.get_core_config = lambda: SimpleNamespace(
            http_router=SimpleNamespace(
                enable_http_router=False, http_router_host="127.0.0.1", http_router_port=8010
            )
        )
        monkeypatch.setitem(sys.modules, "src.app.plugin_system.api.config_api", fake_config_api)
        from src.app.plugin_system.api import config_api as api_pkg  # type: ignore

        monkeypatch.setattr(api_pkg, "config_api", fake_config_api, raising=False)

        from group_notebook.webui_standalone import resolve_http_base_url

        assert resolve_http_base_url() == ""

    def test_falls_back_to_core_config_port(self, monkeypatch):
        """无运行时单例时读取 core.toml 的 [http_router] 端口。"""
        import sys
        import types

        fake_config_api = types.ModuleType("src.app.plugin_system.api.config_api")
        fake_config_api.get_core_config = lambda: SimpleNamespace(
            http_router=SimpleNamespace(
                enable_http_router=True, http_router_host="192.168.8.8", http_router_port=9555
            )
        )
        monkeypatch.setitem(sys.modules, "src.app.plugin_system.api.config_api", fake_config_api)
        from src.app.plugin_system.api import config_api as api_pkg  # type: ignore

        monkeypatch.setattr(api_pkg, "config_api", fake_config_api, raising=False)

        from group_notebook.webui_standalone import resolve_http_base_url

        assert resolve_http_base_url() == "http://192.168.8.8:9555"


class TestDossierEndpointRegression:
    def test_generate_dossier_no_nameerror(self, client, cover_tmp):
        """回归：端点里 group/group_id 变量名写错导致裸 500（v0.5.0 真机事故）。

        带假服务时必须正常进入端点内部并返回 200，而不是 NameError 500。
        """
        class _Service(_FakeService):
            async def generate_dossier(self, stream_id, week_monday="", *, room_name="",
                                       members=None, direction="", force=False):
                return True, "案底已生成", None

        app = create_app(_plugin(notebook_service=_Service()))
        web_client = TestClient(app)
        response = web_client.post(
            "/api/generate_dossier",
            json={"group_id": "123456", "date": "2026-11-05", "members": [],
                  "send": False},
        )
        assert response.status_code == 200
        assert response.json()["data"]["ok"] is True

    def test_generate_dossier_send_path(self, client, cover_tmp):
        class _Service(_FakeService):
            async def send_dossier(self, stream_id, week_monday="", *, room_name="",
                                   members=None, direction="", regenerate=False):
                return True, "案底已发出"

        app = create_app(_plugin(notebook_service=_Service()))
        web_client = TestClient(app)
        response = web_client.post(
            "/api/generate_dossier",
            json={"group_id": "123456", "date": "2026-11-05", "members": [],
                  "send": True},
        )
        assert response.status_code == 200
        assert "已发出" in response.json()["message"]
