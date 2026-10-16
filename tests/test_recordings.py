"""麦克风录音的服务端存档。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.config import get_settings
from app.core.recordings import (
    build_filename,
    list_recordings,
    resolve_recording,
    save_recording,
)
from tests.conftest import encode_webm_opus, make_tone


@pytest.fixture
def settings_with_tmp_recordings(tmp_path):
    base = get_settings()
    return replace(base, recordings_dir=tmp_path / "recordings", save_recordings=True)


@pytest.fixture
def recording_client(client, settings_with_tmp_recordings):
    """把应用的配置指向临时存档目录，避免污染真实 recordings/。"""
    client.app.dependency_overrides[get_settings] = lambda: settings_with_tmp_recordings
    yield client, settings_with_tmp_recordings
    client.app.dependency_overrides.clear()


def _post(client, payload: bytes, language: str = "zh", source: str | None = None):
    data = {"language": language}
    if source is not None:
        data["source"] = source
    return client.post(
        "/api/transcribe",
        files={"file": ("recording.webm", payload, "audio/webm")},
        data=data,
    )


# --------------------------------------------------------------- 文件名与扩展名


def test_filename_contains_timestamp_language_and_safe_chars():
    name = build_filename(b"\x1aE\xdf\xa3" + b"\x00" * 32, "zh", None)
    assert name.endswith(".webm")
    assert "-zh-" in name
    # 只含安全字符，可作为 URL 路径片段
    assert all(ch.isalnum() or ch in ".-_" for ch in name)


def test_extension_follows_content_not_filename():
    """扩展名按内容嗅探决定，不被上传文件名误导。"""
    wav = b"RIFF\x00\x00\x00\x00WAVEfmt "
    assert build_filename(wav, "en", "totally.m4a").endswith(".wav")
    # 无法识别时回退到原文件名后缀
    assert build_filename(b"plain text", "en", "clip.m4a").endswith(".m4a")
    # 两者都不可用时用 .bin 兜底
    assert build_filename(b"plain text", "en", None).endswith(".bin")


# --------------------------------------------------------------- 存档与列举


def test_save_and_list_recordings(settings_with_tmp_recordings, tone_wav):
    assert list_recordings(settings_with_tmp_recordings) == []

    first = save_recording(
        tone_wav, language="zh", settings=settings_with_tmp_recordings, fallback_name="a.wav"
    )
    assert first is not None
    saved_path = Path(settings_with_tmp_recordings.recordings_dir) / first.filename
    assert saved_path.read_bytes() == tone_wav  # 原样保存
    assert first.size_bytes == len(tone_wav)

    second = save_recording(
        tone_wav, language="en", settings=settings_with_tmp_recordings, fallback_name="b.wav"
    )
    listed = list_recordings(settings_with_tmp_recordings)
    assert len(listed) == 2
    # 最新在前
    assert listed[0].created_at >= listed[1].created_at
    assert {item.filename for item in listed} == {first.filename, second.filename}


def test_save_is_skipped_when_disabled(settings_with_tmp_recordings, tone_wav):
    disabled = replace(settings_with_tmp_recordings, save_recordings=False)
    assert save_recording(tone_wav, language="zh", settings=disabled) is None
    assert not Path(disabled.recordings_dir).exists()


def test_list_recordings_on_missing_dir_returns_empty(settings_with_tmp_recordings):
    assert list_recordings(settings_with_tmp_recordings) == []


# --------------------------------------------------------------- 路径穿越防护


@pytest.mark.parametrize(
    "bad",
    [
        "../secret.wav",
        "..\\secret.wav",
        "sub/dir.wav",
        "..",
        ".",
        "",
        "/etc/passwd",
        "C:\\Windows\\win.ini",
        "name with space.wav",
        ".hidden",
    ],
)
def test_resolve_rejects_unsafe_names(settings_with_tmp_recordings, bad):
    assert resolve_recording(bad, settings_with_tmp_recordings) is None


def test_resolve_returns_path_inside_recordings_dir(settings_with_tmp_recordings, tone_wav):
    saved = save_recording(tone_wav, language="zh", settings=settings_with_tmp_recordings)
    path = resolve_recording(saved.filename, settings_with_tmp_recordings)
    assert path is not None
    assert path.parent == Path(settings_with_tmp_recordings.recordings_dir).resolve()
    assert resolve_recording("no-such-file.wav", settings_with_tmp_recordings) is None


# --------------------------------------------------------------- HTTP 接口


def test_recording_is_saved_only_when_source_is_recording(
    recording_client, settings_with_tmp_recordings, tone_wav
):
    client, _ = recording_client

    assert _post(client, tone_wav, source="upload").status_code == 200
    assert _post(client, tone_wav).status_code == 200  # 缺省视为上传，不存档
    assert list_recordings(settings_with_tmp_recordings) == []

    assert _post(client, tone_wav, source="recording").status_code == 200
    listed = list_recordings(settings_with_tmp_recordings)
    assert len(listed) == 1
    assert listed[0].size_bytes == len(tone_wav)


def test_recording_saved_even_if_recognition_fails(
    recording_client, settings_with_tmp_recordings
):
    """无法识别的音频也要留档，方便排查"录到了但识别不了"的问题。"""
    client, _ = recording_client
    response = _post(client, b"this is not audio at all", source="recording")
    assert response.status_code == 415
    assert len(list_recordings(settings_with_tmp_recordings)) == 1


def test_list_and_fetch_recording_via_api(recording_client, tone_wav):
    client, settings = recording_client
    _post(client, tone_wav, source="recording")

    listing = client.get("/api/recordings")
    assert listing.status_code == 200
    items = listing.json()["recordings"]
    assert len(items) == 1
    assert {"filename", "size_bytes", "created_at", "url"} == set(items[0])

    fetch = client.get(items[0]["url"])
    assert fetch.status_code == 200
    assert fetch.content == tone_wav


def test_list_recordings_respects_limit(recording_client, tone_wav):
    client, _ = recording_client
    for _ in range(3):
        _post(client, tone_wav, source="recording")
    assert len(client.get("/api/recordings").json()["recordings"]) == 3
    assert len(client.get("/api/recordings?limit=2").json()["recordings"]) == 2


@pytest.mark.parametrize("bad", ["..%2Fsecret.wav", "nope.wav", "%2e%2e"])
def test_fetch_unknown_recording_returns_404(recording_client, bad):
    client, _ = recording_client
    response = client.get(f"/api/recordings/{bad}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_info_reports_recording_support(client):
    body = client.get("/api/info").json()
    assert "save_recordings" in body


def test_recording_of_realistic_browser_format(recording_client):
    """Chrome 产出的 WebM/Opus 也能正确存档并回放。"""
    try:
        payload = encode_webm_opus(make_tone(1.0), 16000)
    except Exception:  # pragma: no cover
        pytest.skip("PyAV 缺少 libopus 编码器")

    client, settings = recording_client
    assert _post(client, payload, source="recording").status_code == 200
    listed = list_recordings(settings)
    assert len(listed) == 1
    assert listed[0].filename.endswith(".webm")
    assert client.get(listed[0].url).content == payload
