"""音频解码层单元测试（不经过 HTTP）。"""

from __future__ import annotations

import numpy as np
import pytest

from app.core.audio import (
    _decode_wav_stdlib,
    decode_audio,
    encode_wav,
    sniff_container,
)
from app.errors import (
    DecodeFailedError,
    EmptyFileError,
    FileTooLargeError,
    UnsupportedFormatError,
)
from tests.conftest import SAMPLE_RATE, encode_webm_opus, make_silence, make_tone


# --------------------------------------------------------------- 正常路径


def test_decode_wav_returns_16k_mono_float32(tone_wav):
    audio = decode_audio(tone_wav)
    assert audio.sample_rate == 16000
    assert audio.samples.dtype == np.float32
    assert audio.samples.ndim == 1
    assert audio.duration_seconds == pytest.approx(1.0, abs=0.01)
    assert audio.container == "wav"


def test_decode_resamples_44100_to_16000():
    samples = make_tone(2.0, rate=44100)
    audio = decode_audio(encode_wav(samples, 44100))
    assert audio.sample_rate == 16000
    assert audio.original_sample_rate == 44100
    assert audio.duration_seconds == pytest.approx(2.0, abs=0.05)


def test_decode_downmixes_stereo():
    left = make_tone(1.0, freq=440.0, rate=SAMPLE_RATE)
    right = make_tone(1.0, freq=880.0, rate=SAMPLE_RATE)
    interleaved = np.empty(left.size * 2, dtype=np.float32)
    interleaved[0::2] = left
    interleaved[1::2] = right

    audio = decode_audio(encode_wav(interleaved, SAMPLE_RATE, channels=2))
    assert audio.original_channels == 2
    assert audio.samples.ndim == 1
    assert audio.duration_seconds == pytest.approx(1.0, abs=0.02)


def test_decode_webm_opus(tone_samples):
    """Chrome MediaRecorder 的真实输出格式。"""
    try:
        payload = encode_webm_opus(tone_samples, SAMPLE_RATE)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PyAV 缺少 libopus 编码器：{exc}")

    audio = decode_audio(payload)
    assert audio.container == "webm"
    assert audio.sample_rate == 16000
    assert audio.duration_seconds == pytest.approx(1.0, abs=0.2)
    assert np.max(np.abs(audio.samples)) > 0.01


def test_stdlib_wav_fallback_matches_pyav():
    """标准库兜底路径解出的采样点应与 PyAV 路径基本一致。"""
    samples = make_tone(0.5, freq=220.0)
    payload = encode_wav(samples, SAMPLE_RATE)

    primary = decode_audio(payload)
    fallback = _decode_wav_stdlib(payload, SAMPLE_RATE)
    assert fallback is not None
    fallback_samples, rate, channels = fallback
    assert rate == SAMPLE_RATE and channels == 1
    assert fallback_samples.shape == primary.samples.shape
    # 两条路径都应是"无损"，波形几乎完全一致
    assert np.allclose(fallback_samples, primary.samples, atol=1e-3)


def test_encode_wav_roundtrip():
    samples = make_tone(0.25, freq=330.0)
    audio = decode_audio(encode_wav(samples, SAMPLE_RATE))
    assert audio.num_samples == samples.size


# --------------------------------------------------------------- 异常路径


def test_empty_bytes_raises_empty_file():
    with pytest.raises(EmptyFileError):
        decode_audio(b"")


def test_oversized_bytes_raises_file_too_large(tone_wav):
    with pytest.raises(FileTooLargeError):
        decode_audio(tone_wav, max_bytes=10)


def test_corrupt_wav_raises_decode_failed(corrupt_wav):
    with pytest.raises(DecodeFailedError):
        decode_audio(corrupt_wav)


def test_plain_text_raises_unsupported_format(not_audio):
    with pytest.raises(UnsupportedFormatError):
        decode_audio(not_audio)


def test_amr_is_reported_as_unsupported():
    with pytest.raises(UnsupportedFormatError):
        decode_audio(b"#!AMR\n\x00\x00\x00\x00")


def test_silence_decodes_without_error():
    audio = decode_audio(encode_wav(make_silence(1.0), SAMPLE_RATE))
    assert audio.is_silent
    assert audio.duration_seconds == pytest.approx(1.0, abs=0.01)


# --------------------------------------------------------------- 格式嗅探


@pytest.mark.parametrize(
    "payload,expected",
    [
        (b"RIFF\x00\x00\x00\x00WAVEfmt ", "wav"),
        (b"\x1aE\xdf\xa3\x01\x00\x00\x00", "webm"),
        (b"OggS\x00\x02\x00\x00", "ogg"),
        (b"fLaC\x00\x00\x00\x22", "flac"),
        (b"ID3\x03\x00\x00\x00", "mp3"),
        (b"\x00\x00\x00\x18ftypM4A ", "mp4"),
        (b"totally unknown bytes", "unknown"),
    ],
)
def test_sniff_container(payload, expected):
    assert sniff_container(payload) == expected
