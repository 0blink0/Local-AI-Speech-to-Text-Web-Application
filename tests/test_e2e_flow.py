"""前后端流程与端到端验证。

覆盖题目 §6.1 的"前后端流程：至少验证实际录音或上传识别流程"。
这里用 TestClient 走完整的 HTTP/WebSocket 链路（音频由合成信号代替，
避免依赖真实麦克风硬件）。
"""

from __future__ import annotations

import re

import numpy as np

from app.core.audio import encode_wav
from tests.conftest import PROJECT_ROOT, SAMPLE_RATE, make_tone


def _upload(client, payload: bytes, language: str, filename: str = "clip.wav"):
    return client.post(
        "/api/transcribe",
        files={"file": (filename, payload, "audio/wav")},
        data={"language": language},
    )


# --------------------------------------------------------------- HTTP 全流程


def test_upload_then_transcribe_flow(client):
    """模拟"选择文件 → 上传 → 展示结果"的完整前端流程。"""
    payload = encode_wav(make_tone(3.0), SAMPLE_RATE)

    response = _upload(client, payload, "zh")

    assert response.status_code == 200
    body = response.json()
    assert body["language"] == "zh"
    assert body["text"]
    assert body["duration_seconds"] > 0
    assert body["processing_seconds"] >= 0


def test_recording_then_transcribe_flow(client):
    """模拟"停止录音 → 提交音频 blob → 展示结果"，音频为浏览器录音格式。"""
    from tests.conftest import encode_webm_opus

    try:
        blob = encode_webm_opus(make_tone(2.0), SAMPLE_RATE)
    except Exception:  # pragma: no cover
        import pytest

        pytest.skip("PyAV 缺少 libopus 编码器")

    response = _upload(client, blob, "en", filename="recording.webm")
    assert response.status_code == 200
    assert response.json()["text"]


def test_bilingual_round_trip(client):
    """中英文各识别一次，然后再次识别 —— 验证可重复使用。"""
    payload = encode_wav(make_tone(1.5), SAMPLE_RATE)

    first = _upload(client, payload, "zh").json()
    second = _upload(client, payload, "en").json()
    third = _upload(client, payload, "zh").json()

    assert first["language"] == "zh"
    assert second["language"] == "en"
    assert third["language"] == "zh"
    assert first["text"] != second["text"]


def test_frontend_contract_references_required_endpoints():
    """前端必须调用题目规定的接口，且不依赖选填的实时通道。"""
    app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    index_html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")

    # 录音与上传两条入口都必须走后端识别接口
    assert "/api/transcribe" in app_js
    assert "/api/info" in app_js
    # 语言选择器必须同时提供 zh 与 en
    assert 'value="zh"' in index_html
    assert 'value="en"' in index_html
    # 必须有开始/停止录音按钮与复制能力
    assert "btn-start" in index_html and "btn-stop" in index_html
    assert "btn-copy" in index_html
    # 选中文件后需要醒目的文件名展示区（填满虚线框、放大文件名）
    assert 'id="dz-file-name"' in index_html and 'id="dz-selected"' in index_html
    assert 'id="btn-clear-file"' in index_html

    # 实时识别前端暂未启用（后端接口与测试仍保留，见 README）。
    # 这里锁定该决定，避免出现"注释里提到所以测试通过"的假阳性。
    assert "/ws/stream" not in _code_only(app_js)
    assert 'id="realtime"' not in index_html
    assert "realtime-card" not in index_html


def _code_only(source: str) -> str:
    """去掉块注释与行注释后的代码文本，避免注释内容影响契约断言。"""
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", without_block, flags=re.M)


# --------------------------------------------------------------- WebSocket 流式


def _pcm16(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()


def test_realtime_streaming_flow(client):
    """F6：start → 分片音频 → partial → stop → final。"""
    with client.websocket_connect("/ws/stream") as websocket:
        ready = websocket.receive_json()
        assert ready["type"] == "ready"
        assert ready["sample_rate"] == 16000

        websocket.send_json({"type": "start", "language": "zh"})
        started = websocket.receive_json()
        assert started["type"] == "started"
        assert started["language"] == "zh"

        # 第 1 秒
        websocket.send_bytes(_pcm16(make_tone(1.0)))
        first = websocket.receive_json()
        assert first["type"] == "partial"
        assert first["text"]
        assert first["duration_seconds"] == 1.0

        # 第 2 秒
        websocket.send_bytes(_pcm16(make_tone(1.0)))
        second = websocket.receive_json()
        assert second["type"] == "partial"
        assert second["duration_seconds"] == 2.0

        # 结束
        websocket.send_json({"type": "stop"})
        final = websocket.receive_json()
        assert final["type"] == "final"
        assert final["text"]
        assert final["duration_seconds"] == 2.0
        assert final["processing_seconds"] >= 0


def test_realtime_streaming_english(client):
    with client.websocket_connect("/ws/stream") as websocket:
        websocket.receive_json()  # ready
        websocket.send_json({"type": "start", "language": "en"})
        assert websocket.receive_json()["language"] == "en"

        websocket.send_bytes(_pcm16(make_tone(1.5)))
        assert websocket.receive_json()["type"] == "partial"

        websocket.send_json({"type": "stop"})
        assert websocket.receive_json()["type"] == "final"


def test_realtime_invalid_language_reports_error(client):
    with client.websocket_connect("/ws/stream") as websocket:
        websocket.receive_json()  # ready
        websocket.send_json({"type": "start", "language": "xx"})
        message = websocket.receive_json()
        assert message["type"] == "error"
        assert message["code"] == "INVALID_LANGUAGE"


def test_realtime_ignores_short_silence_and_finishes_empty(client):
    """低于最小语音长度 → final 为空文本，不报错。"""
    with client.websocket_connect("/ws/stream") as websocket:
        websocket.receive_json()  # ready
        websocket.send_json({"type": "start", "language": "zh"})
        websocket.receive_json()  # started

        websocket.send_bytes(_pcm16(np.zeros(1600, dtype=np.float32)))  # 0.1s 静音
        websocket.send_json({"type": "stop"})
        final = websocket.receive_json()
        assert final["type"] == "final"
        assert final["text"] == ""


def test_realtime_bad_control_frame(client):
    with client.websocket_connect("/ws/stream") as websocket:
        websocket.receive_json()  # ready
        websocket.send_text("not-json")
        message = websocket.receive_json()
        assert message["type"] == "error"
        assert message["code"] == "BAD_MESSAGE"


def test_realtime_unknown_action(client):
    with client.websocket_connect("/ws/stream") as websocket:
        websocket.receive_json()  # ready
        websocket.send_json({"type": "explode"})
        message = websocket.receive_json()
        assert message["type"] == "error"
        assert message["code"] == "UNKNOWN_ACTION"
