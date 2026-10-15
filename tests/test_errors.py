"""异常处理与错误响应契约（F5 / 题目 §6.1）。"""

from __future__ import annotations

import numpy as np
import pytest

from app.asr import registry
from app.asr.base import ASREngine


class _FailingInferenceEngine(ASREngine):
    """能加载，但推理时抛异常 → 对应 500。"""

    name = "failing"

    def __init__(self) -> None:
        self._loaded = False

    def load(self) -> None:
        self._loaded = True

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def transcribe(self, samples, sample_rate, language=None):
        raise RuntimeError("模拟推理崩溃")


class _UnavailableEngine(ASREngine):
    name = "unavailable"

    def load(self) -> None:
        raise RuntimeError("权重不存在")

    @property
    def is_loaded(self) -> bool:
        return False

    def transcribe(self, samples, sample_rate, language=None):  # pragma: no cover
        raise RuntimeError("不可达")


def _post(client, payload: bytes, language: str = "zh", filename: str = "a.wav"):
    return client.post(
        "/api/transcribe",
        files={"file": (filename, payload, "application/octet-stream")},
        data={"language": language},
    )


def test_unsupported_format_returns_415(client, not_audio):
    response = _post(client, not_audio)
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_FORMAT"


def test_corrupt_audio_returns_400(client, corrupt_wav):
    response = _post(client, corrupt_wav)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DECODE_FAILED"


def test_file_too_large_returns_413(client):
    response = _post(client, b"\x00" * (10 * 1024 * 1024 + 1))
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_model_unavailable_returns_503(client, tone_wav):
    registry.set_engine(_UnavailableEngine())
    response = _post(client, tone_wav)
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "MODEL_UNAVAILABLE"
    assert "模型" in body["error"]["message"]


def test_inference_failure_returns_500(client, tone_wav):
    registry.set_engine(_FailingInferenceEngine())
    response = _post(client, tone_wav)
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INFERENCE_FAILED"
    assert body["error"]["message"]


def test_health_still_works_when_model_unavailable(client):
    """F5：模型不可用不应导致整个页面/服务不可用。"""
    registry.set_engine(_UnavailableEngine())
    assert client.get("/health").status_code == 200
    assert client.get("/api/info").json()["load_error"] is None  # info 本身不做加载


def test_method_not_allowed_returns_structured_error(client):
    response = client.get("/api/transcribe")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


@pytest.mark.parametrize("code", ["MISSING_FILE", "INVALID_LANGUAGE", "EMPTY_FILE"])
def test_client_errors_have_readable_messages(client, tone_wav, code):
    if code == "MISSING_FILE":
        response = client.post("/api/transcribe", data={"language": "zh"})
    elif code == "INVALID_LANGUAGE":
        response = _post(client, tone_wav, "xx")
    else:
        response = _post(client, b"")
    assert response.json()["error"]["code"] == code
    assert len(response.json()["error"]["message"]) > 0


def test_silence_does_not_crash(client, silence_wav):
    """F5：无有效语音时返回空结果而不是崩溃。"""
    response = _post(client, silence_wav)
    assert response.status_code == 200
    assert response.json()["text"] == ""


def test_streaming_endpoint_reports_error_before_start(client):
    """未 start 就发音频 → 结构化错误而不是断连。"""
    with client.websocket_connect("/ws/stream") as websocket:
        assert websocket.receive_json()["type"] == "ready"
        websocket.send_bytes(np.zeros(1600, dtype="<i2").tobytes())
        message = websocket.receive_json()
        assert message["type"] == "error"
        assert message["code"] == "NOT_STARTED"
