"""pytest 全局夹具。

关键约束（题目 §6.1）：

* ``python -m pytest -q`` 必须 **不联网、不下载模型、不依赖麦克风硬件**。
  因此这里强制把引擎设为 ``mock``。
* 真实模型测试放在 ``test_engine_real.py``，用 ``-m real_model`` 显式开启，
  走独立的引擎实例，不经过这里的 mock 注入。
"""

from __future__ import annotations

import io
import os
from pathlib import Path

# 必须在导入 app 之前设置：app.main 在导入时就会读取环境变量构建应用
os.environ["ASR_ENGINE"] = "mock"
os.environ["ASR_WARMUP"] = "1"
os.environ["LOG_LEVEL"] = os.environ.get("LOG_LEVEL", "WARNING")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from app.core.audio import encode_wav  # noqa: E402

SAMPLE_RATE = 16000
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = PROJECT_ROOT / "samples"
REFERENCE_FILE = SAMPLES_DIR / "reference.txt"


# ----------------------------------------------------------------- 音频构造工具


def make_tone(
    seconds: float = 1.0,
    freq: float = 440.0,
    rate: int = SAMPLE_RATE,
    amplitude: float = 0.3,
) -> np.ndarray:
    """生成一段正弦波，用于替代"有人说话"的音频（仅需非静音）。"""
    t = np.arange(int(seconds * rate)) / float(rate)
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def make_silence(seconds: float = 1.0, rate: int = SAMPLE_RATE) -> np.ndarray:
    return np.zeros(int(seconds * rate), dtype=np.float32)


def encode_webm_opus(samples: np.ndarray, rate: int = SAMPLE_RATE) -> bytes:
    """把 float32 单声道音频编码为 WebM/Opus —— 即 Chrome 录制的真实格式。

    若当前 PyAV 构建不含 libopus 编码器则抛出 RuntimeError，
    调用方应 ``pytest.skip``。
    """
    import av

    buffer = io.BytesIO()
    with av.open(buffer, "w", format="webm") as container:
        stream = container.add_stream("libopus", rate=48000)
        stream.layout = "mono"
        frame = av.AudioFrame.from_ndarray(
            np.asarray(samples, dtype=np.float32).reshape(1, -1), format="flt", layout="mono"
        )
        frame.sample_rate = rate
        frame.pts = 0
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buffer.getvalue()


# ----------------------------------------------------------------- 自动夹具


@pytest.fixture(autouse=True)
def isolated_state():
    """每个用例前后重置引擎单例与配置缓存，保证用例之间互不影响。"""
    from app.asr import registry
    from app.config import get_settings

    registry.reset_engine()
    get_settings.cache_clear()
    yield
    registry.reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def client():
    """带 lifespan 的 TestClient（会触发一次 mock 引擎预热）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


# ----------------------------------------------------------------- 音频夹具


@pytest.fixture
def tone_samples() -> np.ndarray:
    return make_tone(1.0)


@pytest.fixture
def tone_wav(tone_samples: np.ndarray) -> bytes:
    return encode_wav(tone_samples, SAMPLE_RATE)


@pytest.fixture
def long_wav() -> bytes:
    """5 秒音频，用于校验时长计算。"""
    return encode_wav(make_tone(5.0), SAMPLE_RATE)


@pytest.fixture
def silence_wav() -> bytes:
    return encode_wav(make_silence(1.0), SAMPLE_RATE)


@pytest.fixture
def webm_opus(tone_samples: np.ndarray) -> bytes:
    try:
        return encode_webm_opus(tone_samples, SAMPLE_RATE)
    except Exception as exc:  # pragma: no cover - 取决于 PyAV 构建
        pytest.skip(f"当前 PyAV 构建不支持 libopus 编码，跳过 WebM 用例：{exc}")


@pytest.fixture
def zero_byte_file() -> bytes:
    return b""


@pytest.fixture
def corrupt_wav() -> bytes:
    """看起来像 WAV（魔数正确）但内容损坏，两种解码器都应失败。"""
    return b"RIFF\x00\x00\x00\x00WAVEjunkdata\x00\x01\x02\x03"


@pytest.fixture
def not_audio() -> bytes:
    """既不是已知音频容器，也无法解码。"""
    return b"this is a plain text file, definitely not audio at all."


# ----------------------------------------------------------------- 真实样本

ZH_SAMPLE = SAMPLES_DIR / "chinese.wav"
EN_SAMPLE = SAMPLES_DIR / "english.wav"


def read_reference_texts() -> dict[str, str]:
    """解析 ``samples/reference.txt``。

    格式约定（每行一条）::

        [zh] 你好，欢迎使用语音识别系统。
        [en] Hello, welcome to the speech recognition system.
    """
    if not REFERENCE_FILE.exists():
        return {}
    result: dict[str, str] = {}
    for line in REFERENCE_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or not line.startswith("["):
            continue
        try:
            key, value = line[1:].split("]", 1)
        except ValueError:
            continue
        result[key.strip().lower()] = value.strip()
    return result


@pytest.fixture(scope="session")
def real_samples() -> dict[str, Path]:
    """真实中英文样本路径；缺失时跳过依赖它的用例。"""
    missing = [p.name for p in (ZH_SAMPLE, EN_SAMPLE) if not p.exists()]
    if missing:
        pytest.skip(
            "缺少真实测试音频 " + ", ".join(missing) + "（见 README「测试音频」一节）"
        )
    return {"zh": ZH_SAMPLE, "en": EN_SAMPLE}
