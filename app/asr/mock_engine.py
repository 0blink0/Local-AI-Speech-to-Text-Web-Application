"""确定性 Mock 引擎。

用途：

* 让 ``python -m pytest -q`` 在不下载模型、不联网的前提下跑通全部 API 流程；
* 让前端联调时不需要等待真实模型推理；
* 通过 ``fail_with`` 注入异常，覆盖"模型失败"路径。

默认输出带 ``[mock]`` 前缀，避免把假结果误认为真实识别结果。
"""

from __future__ import annotations

import os

import numpy as np

from app.asr.base import ASREngine, ASRResult, ASRSegment

DEFAULT_TRANSCRIPTS: dict[str, str] = {
    "zh": "[mock] 这是模拟识别的中文结果。",
    "en": "[mock] This is a mocked English transcription.",
    # 自动检测（language=None）时的兜底
    "auto": "[mock] Mock transcription with auto language detection.",
}


class MockEngine(ASREngine):
    """不依赖任何模型权重的桩引擎。"""

    name = "mock"

    def __init__(
        self,
        transcripts: dict[str, str] | None = None,
        *,
        fail_with: Exception | None = None,
        latency_seconds: float = 0.0,
    ) -> None:
        self._transcripts = dict(DEFAULT_TRANSCRIPTS)
        if transcripts:
            self._transcripts.update(transcripts)
        # 允许用环境变量覆盖，方便手工联调
        env_text = os.getenv("MOCK_ASR_TEXT", "").strip()
        if env_text:
            self._transcripts["zh"] = env_text
            self._transcripts["en"] = env_text
            self._transcripts["auto"] = env_text
        if os.getenv("MOCK_ASR_FAIL", "").strip() in {"1", "true", "yes"}:
            fail_with = RuntimeError("MOCK_ASR_FAIL 已启用：模拟推理失败")
        self._fail_with = fail_with
        self._latency = max(0.0, latency_seconds)
        self._loaded = False

    # ------------------------------------------------------------------ 生命周期
    def load(self) -> None:
        self._loaded = True

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    # ------------------------------------------------------------------ 识别
    def transcribe(
        self,
        samples: np.ndarray,
        sample_rate: int,
        language: str | None = None,
    ) -> ASRResult:
        if not self._loaded:
            self.load()

        if self._latency:
            import time

            time.sleep(self._latency)

        if self._fail_with is not None:
            raise self._fail_with

        key = language if language in ("zh", "en") else "auto"
        text = self._transcripts.get(key, "")

        # 空音频 / 全静音 → 空结果，模拟"音频中没有可识别语音"的行为
        if samples is None or np.asarray(samples).size == 0:
            text = ""
        elif float(np.max(np.abs(np.asarray(samples, dtype=np.float32)))) < 1e-4:
            text = ""

        duration = 0.0
        if samples is not None and sample_rate:
            duration = float(np.asarray(samples).shape[0]) / float(sample_rate)
        segments = (ASRSegment(start=0.0, end=duration, text=text),) if text else ()
        return ASRResult(text=text, language=language or "auto", segments=segments)
