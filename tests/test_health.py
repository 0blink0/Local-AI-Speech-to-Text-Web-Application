"""健康检查、页面与静态资源。"""

from __future__ import annotations


def test_health_returns_ok(client):
    """GET /health 必须严格返回 {"status": "ok"}（题目 §4.1）。"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_info_endpoint_exposes_engine_state(client):
    response = client.get("/api/info")
    assert response.status_code == 200
    body = response.json()
    assert body["engine"] == "mock"
    assert body["sample_rate"] == 16000
    assert body["max_upload_mb"] == 10
    assert body["supported_languages"] == ["zh", "en"]


def test_index_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "本地语音识别" in response.text


def test_static_assets_are_served(client):
    for path in ("/static/app.js", "/static/style.css", "/static/pcm-worklet.js"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.content, path


def test_unknown_route_returns_structured_error(client):
    response = client.get("/definitely-not-a-route")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"]
