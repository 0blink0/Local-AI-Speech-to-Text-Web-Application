"""POST /api/transcribe 的正常与非法请求。"""

from __future__ import annotations

import pytest

from tests.conftest import SAMPLE_RATE, encode_webm_opus, make_tone


def _post(client, data: bytes, language: str | None = "zh", filename: str = "audio.wav"):
    files = {"file": (filename, data, "application/octet-stream")}
    form = {} if language is None else {"language": language}
    return client.post("/api/transcribe", files=files, data=form)


# --------------------------------------------------------------- 正常请求


def test_transcribe_wav_chinese(client, tone_wav):
    response = _post(client, tone_wav, "zh")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"text", "language", "duration_seconds", "processing_seconds"}
    assert body["language"] == "zh"
    assert body["text"]
    assert body["duration_seconds"] == pytest.approx(1.0, abs=0.05)
    assert body["processing_seconds"] >= 0


def test_transcribe_wav_english(client, tone_wav):
    response = _post(client, tone_wav, "en")
    assert response.status_code == 200
    body = response.json()
    assert body["language"] == "en"
    assert body["text"]


def test_duration_reflects_real_audio_length(client, long_wav):
    body = _post(client, long_wav, "zh").json()
    assert body["duration_seconds"] == pytest.approx(5.0, abs=0.05)


def test_transcribe_browser_recording_format(client, webm_opus):
    """F2：必须同时支持前端实际使用的浏览器录音格式（WebM/Opus）。"""
    response = _post(client, webm_opus, "zh", filename="recording.webm")
    assert response.status_code == 200
    body = response.json()
    assert body["duration_seconds"] == pytest.approx(1.0, abs=0.2)
    assert body["text"]


def test_transcribe_accepts_language_aliases(client, tone_wav):
    """zh-CN / en-US 这类写法应被容错归一化。"""
    assert _post(client, tone_wav, "zh-CN").json()["language"] == "zh"
    assert _post(client, tone_wav, "en-US").json()["language"] == "en"


def test_can_transcribe_repeatedly(client, tone_wav):
    """F3.5：一次识别完成后可以继续识别下一段音频。"""
    for _ in range(3):
        assert _post(client, tone_wav, "zh").status_code == 200


def test_silent_audio_returns_empty_text_without_error(client, silence_wav):
    """F5：音频中没有可识别语音时不崩溃，返回空结果。"""
    response = _post(client, silence_wav, "zh")
    assert response.status_code == 200
    assert response.json()["text"] == ""


def test_mono_8k_and_stereo_44k_are_resampled(client):
    """服务端统一重采样为 16 kHz 单声道。"""
    stereo = make_tone(2.0, rate=44100)
    interleaved = stereo.reshape(-1, 1).repeat(2, axis=1).reshape(-1)
    from app.core.audio import encode_wav

    body = _post(client, encode_wav(interleaved, 44100, channels=2), "zh").json()
    assert body["duration_seconds"] == pytest.approx(2.0, abs=0.1)


# --------------------------------------------------------------- 非法请求


def test_missing_file_returns_400(client):
    response = client.post("/api/transcribe", data={"language": "zh"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MISSING_FILE"


def test_missing_language_returns_400(client, tone_wav):
    response = _post(client, tone_wav, None)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_LANGUAGE"


def test_invalid_language_returns_400(client, tone_wav):
    response = _post(client, tone_wav, "fr")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_LANGUAGE"


def test_empty_file_returns_400(client):
    response = _post(client, b"", "zh")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "EMPTY_FILE"


def test_oversized_file_returns_413(client):
    """后端必须限制上传大小（建议 10 MB）。"""
    payload = b"\x00" * (10 * 1024 * 1024 + 1024)
    response = _post(client, payload, "zh")
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_error_body_shape_is_consistent(client):
    response = client.post("/api/transcribe", data={"language": "zh"})
    body = response.json()
    assert list(body) == ["error"]
    assert set(body["error"]) == {"code", "message"}
    assert isinstance(body["error"]["message"], str)
