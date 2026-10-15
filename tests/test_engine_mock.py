"""ASR 引擎抽象层与 Mock 引擎。"""

from __future__ import annotations

import numpy as np
import pytest

from app.asr import registry
from app.asr.base import ASREngine, ASRResult
from app.asr.mock_engine import DEFAULT_TRANSCRIPTS, MockEngine
from app.config import Settings
from app.errors import ModelUnavailableError


class _BrokenEngine(ASREngine):
    """构造成功但加载失败，用于覆盖"模型加载失败"路径。"""

    name = "broken"

    def __init__(self) -> None:
        self.load_calls = 0

    def load(self) -> None:
        self.load_calls += 1
        raise RuntimeError("模拟加载失败：权重文件缺失")

    @property
    def is_loaded(self) -> bool:
        return False

    def transcribe(self, samples, sample_rate, language=None):  # pragma: no cover
        raise RuntimeError("不可达")


# --------------------------------------------------------------- MockEngine


def test_mock_engine_returns_language_specific_text():
    engine = MockEngine()
    engine.load()
    assert engine.transcribe(np.ones(16000, dtype=np.float32), 16000, "zh").text
    assert engine.transcribe(np.ones(16000, dtype=np.float32), 16000, "en").text
    assert (
        engine.transcribe(np.ones(16000, dtype=np.float32), 16000, "zh").text
        == DEFAULT_TRANSCRIPTS["zh"]
    )


def test_mock_engine_is_deterministic():
    engine = MockEngine()
    audio = np.ones(16000, dtype=np.float32) * 0.5
    first = engine.transcribe(audio, 16000, "zh")
    second = engine.transcribe(audio, 16000, "zh")
    assert first == second


def test_mock_engine_returns_empty_for_silence():
    engine = MockEngine()
    engine.load()
    result = engine.transcribe(np.zeros(16000, dtype=np.float32), 16000, "zh")
    assert result.text == ""
    assert result.is_empty


def test_mock_engine_can_simulate_failure():
    engine = MockEngine(fail_with=RuntimeError("boom"))
    engine.load()
    with pytest.raises(RuntimeError):
        engine.transcribe(np.ones(100, dtype=np.float32), 16000, "zh")


def test_mock_engine_segments_cover_duration():
    engine = MockEngine()
    result = engine.transcribe(np.ones(32000, dtype=np.float32), 16000, "zh")
    assert result.segments
    assert result.segments[0].end == pytest.approx(2.0, abs=0.01)


# --------------------------------------------------------------- 注册表


def test_build_engine_mock():
    engine = registry.build_engine(Settings(engine="mock"))
    assert isinstance(engine, MockEngine)


def test_build_engine_faster_whisper_is_lazy():
    """构建不应触发权重加载。"""
    engine = registry.build_engine(Settings(engine="faster_whisper", model_size="tiny"))
    assert engine.name == "faster_whisper"
    assert engine.is_loaded is False


def test_build_engine_rejects_unknown_name():
    with pytest.raises(ModelUnavailableError):
        registry.build_engine(Settings(engine="not-a-real-engine"))


def test_get_engine_is_singleton():
    first = registry.get_engine()
    second = registry.get_engine()
    assert first is second


def test_ensure_ready_loads_engine():
    engine = registry.ensure_ready()
    assert engine.is_loaded
    assert registry.load_error() is None


def test_ensure_ready_raises_on_load_failure_and_caches_error():
    broken = _BrokenEngine()
    registry.set_engine(broken)

    with pytest.raises(ModelUnavailableError):
        registry.ensure_ready()
    assert broken.load_calls == 1
    assert registry.load_error()

    # 第二次调用应直接使用缓存的错误，不再重复尝试加载
    with pytest.raises(ModelUnavailableError):
        registry.ensure_ready()
    assert broken.load_calls == 1


def test_describe_reports_engine_state():
    engine = registry.ensure_ready()
    described = engine.describe()
    assert described["engine"] == "mock"
    assert described["loaded"] is True


def test_mock_engine_result_type():
    engine = MockEngine()
    result = engine.transcribe(np.ones(10, dtype=np.float32), 16000, "en")
    assert isinstance(result, ASRResult)
