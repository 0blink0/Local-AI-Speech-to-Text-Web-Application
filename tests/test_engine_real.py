"""真实本地模型测试 —— 必须显式开启。

题目 §6.1 要求："必须额外提供使用真实本地模型的中英文识别验证，不能全部依赖 Mock"。

运行方式（会使用/下载真实权重，故默认不参与 ``python -m pytest -q``）::

    python -m pytest -q -m real_model

可选环境变量：

* ``ASR_MODEL``       模型规模（默认 base）
* ``ASR_MODEL_DIR``   权重缓存目录（建议指向项目内 models/，便于离线复现）
* ``ASR_DEVICE``      默认 cpu
* ``ASR_COMPUTE_TYPE``默认 int8
* ``REAL_MODEL_MIN_SIMILARITY`` 与参考文本的最小相似度（默认 0.5）
"""

from __future__ import annotations

import difflib
import os
import re

import pytest

from app.asr import registry
from app.config import get_settings
from app.services.transcribe import transcribe_bytes
from tests.conftest import read_reference_texts

pytestmark = pytest.mark.real_model

# 让"真实模型"测试输出更直观
if not os.getenv("LOG_LEVEL"):
    os.environ["LOG_LEVEL"] = "INFO"


def _build_real_engine():
    from app.asr.faster_whisper_engine import FasterWhisperEngine
    from app.asr.registry import resolve_model_path

    settings = get_settings()
    return FasterWhisperEngine(
        model_size=resolve_model_path(settings),
        device=settings.device,
        compute_type=settings.compute_type,
        download_root=settings.model_dir,
        beam_size=max(1, settings.beam_size),
        initial_prompts={"zh": settings.initial_prompt_zh, "en": settings.initial_prompt_en},
    )


@pytest.fixture(scope="module")
def real_engine():
    """加载真实模型；不可用时跳过而不是失败（便于离线环境）。"""
    engine = _build_real_engine()
    try:
        engine.load()
    except Exception as exc:  # pragma: no cover - 取决于本机模型是否就绪
        pytest.skip(
            "真实模型不可用，跳过真实模型测试。"
            f"请先执行模型初始化（见 README）：{exc}"
        )
    return engine


def _normalize(text: str, language: str) -> str:
    """归一化后再比较，避免标点/大小写差异造成误判。"""
    text = text.strip().lower()
    if language == "zh":
        # 去掉标点与空白，只比较汉字/字母/数字
        return re.sub(r"[^\w\u4e00-\u9fff]", "", text)
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", text).split())


def _similarity(actual: str, expected: str, language: str) -> float:
    a = _normalize(actual, language)
    b = _normalize(expected, language)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _min_similarity() -> float:
    try:
        return float(os.getenv("REAL_MODEL_MIN_SIMILARITY", "0.5"))
    except ValueError:
        return 0.5


@pytest.mark.parametrize("language", ["zh", "en"])
def test_real_model_transcribes_sample(real_engine, real_samples, language):
    """真实模型识别中/英文样本，并与人工参考文本比对。"""
    path = real_samples[language]
    registry.set_engine(real_engine)

    outcome = transcribe_bytes(path.read_bytes(), language, source_name=path.name)

    assert outcome.language == language
    assert outcome.duration_seconds > 1.0
    assert outcome.processing_seconds >= 0
    assert outcome.text, f"真实模型未能识别出任何文本：{path.name}"

    references = read_reference_texts()
    expected = references.get(language)
    if not expected:
        pytest.skip("未提供 samples/reference.txt 参考文本，跳过相似度断言")

    score = _similarity(outcome.text, expected, language)
    print(f"\n[{language}] 识别结果 : {outcome.text}")
    print(f"[{language}] 参考文本 : {expected}")
    print(f"[{language}] 相似度   : {score:.3f}")
    assert score >= _min_similarity(), (
        f"与参考文本的相似度 {score:.3f} 低于阈值 {_min_similarity()}。"
        f"识别结果：{outcome.text!r}"
    )


def test_real_model_via_http_api(client, real_engine, real_samples):
    """真实模型 + 真实 HTTP 接口的端到端验证。"""
    registry.set_engine(real_engine)
    path = real_samples["zh"]

    response = client.post(
        "/api/transcribe",
        files={"file": (path.name, path.read_bytes(), "audio/wav")},
        data={"language": "zh"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["language"] == "zh"
    assert body["duration_seconds"] > 1.0
    assert body["text"]


def test_real_model_handles_silence_gracefully(real_engine):
    """真实模型 + 静音输入：不崩溃，返回空文本。"""
    import numpy as np

    from app.core.audio import encode_wav

    registry.set_engine(real_engine)
    silence = encode_wav(np.zeros(16000, dtype=np.float32), 16000)

    outcome = transcribe_bytes(silence, "zh", source_name="silence.wav")
    assert outcome.text == ""
