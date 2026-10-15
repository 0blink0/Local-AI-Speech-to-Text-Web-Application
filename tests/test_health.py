"""健康检查、页面与静态资源。"""

from __future__ import annotations

import os
from pathlib import Path


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
    # 网络来源固定为国内镜像（唯一来源）
    assert body["hf_endpoint"] == "https://hf-mirror.com"
    assert body["pypi_mirror"].startswith("https://pypi.tuna.tsinghua.edu.cn")


def test_network_source_is_locked_to_domestic_mirror(monkeypatch):
    """网络来源固定为国内镜像：即使外部设置了官方源也会被统一为镜像。"""
    from app.config import HF_MIRROR_ENDPOINT, PYPI_MIRROR, apply_network_source

    monkeypatch.setenv("HF_ENDPOINT", "https://huggingface.co")
    assert apply_network_source() == HF_MIRROR_ENDPOINT
    assert os.environ["HF_ENDPOINT"] == HF_MIRROR_ENDPOINT

    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    assert apply_network_source() == HF_MIRROR_ENDPOINT

    assert PYPI_MIRROR == "https://pypi.tuna.tsinghua.edu.cn/simple"


def test_model_download_uses_modelscope_only():
    """模型下载脚本只保留 ModelScope 一个来源，不提供可切换的其它来源。"""
    root = Path(__file__).resolve().parent.parent
    script = (root / "scripts" / "download_models.py").read_text(encoding="utf-8")
    config = (root / "app" / "config.py").read_text(encoding="utf-8")
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")

    # 来源常量集中在 config，且指向 ModelScope
    assert "MODELSCOPE_ENDPOINT = \"https://modelscope.cn\"" in config
    assert "from app.config import MODELSCOPE_ENDPOINT" in script
    # 不再保留 HuggingFace 代码路径与可切换来源的参数
    assert "hf-mirror.com" not in script
    assert "huggingface.co" not in script
    assert "--source" not in script
    assert "WhisperModel" in script  # 仅用于本地目录自检加载
    # pip 来源同样固定为国内镜像
    assert "--index-url https://pypi.tuna.tsinghua.edu.cn/simple" in requirements


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
