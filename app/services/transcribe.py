"""识别编排服务。

把"校验 → 解码 → 推理 → 计时"串起来，是 HTTP 接口与 WebSocket 接口共用的
唯一识别入口，避免两处逻辑分叉。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import numpy as np

from app.asr.registry import ensure_ready
from app.config import Settings, get_settings
from app.core.audio import AudioData, decode_audio
from app.core.languages import normalize_language
from app.errors import AppError, EmptyFileError, InferenceFailedError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranscribeOutcome:
    """一次识别的完整结果，含时间统计。"""

    text: str
    language: str
    duration_seconds: float
    processing_seconds: float


def _run_inference(audio: AudioData, language: str) -> str:
    """调用引擎并做异常归一化。"""
    engine = ensure_ready()
    try:
        result = engine.transcribe(audio.samples, audio.sample_rate, language)
    except AppError:
        raise
    except Exception as exc:  # 引擎内部异常 → 500 且信息可读
        logger.exception("推理失败")
        raise InferenceFailedError(f"语音识别失败：{exc}") from exc
    return (result.text or "").strip()


def transcribe_bytes(
    data: bytes,
    language: str | None,
    *,
    source_name: str | None = None,
    settings: Settings | None = None,
) -> TranscribeOutcome:
    """识别一段音频字节流（HTTP 上传 / 录音文件走这里）。

    ``processing_seconds`` 覆盖音频解码与模型推理，不含首次模型下载。
    服务启动时已预热模型，因此正常情况下这里不再产生加载耗时。
    """
    settings = settings or get_settings()
    lang = normalize_language(language)

    engine = ensure_ready()  # 未就绪时抛出 503；不计入处理耗时

    started = time.perf_counter()
    audio = decode_audio(
        data,
        target_sr=settings.target_sample_rate,
        max_bytes=settings.max_upload_bytes,
        source_name=source_name,
    )
    try:
        result = engine.transcribe(audio.samples, audio.sample_rate, lang)
    except AppError:
        raise
    except Exception as exc:
        logger.exception("推理失败")
        raise InferenceFailedError(f"语音识别失败：{exc}") from exc
    elapsed = time.perf_counter() - started

    text = (result.text or "").strip()
    logger.info(
        "识别完成 lang=%s duration=%.2fs processing=%.2fs chars=%d source=%s",
        lang,
        audio.duration_seconds,
        elapsed,
        len(text),
        source_name or "-",
    )
    return TranscribeOutcome(
        text=text,
        language=lang,  # 按题目要求回传"本次请求选择的语言"
        duration_seconds=round(audio.duration_seconds, 3),
        processing_seconds=round(elapsed, 3),
    )


def transcribe_samples(
    samples: np.ndarray,
    sample_rate: int,
    language: str | None,
    *,
    processing_seconds: float = 0.0,
) -> tuple[str, float]:
    """识别一段已在内存中的 PCM（WebSocket 流式分片走这里）。

    Returns:
        ``(text, duration_seconds)``
    """
    lang = normalize_language(language)
    audio = AudioData(
        samples=np.asarray(samples, dtype=np.float32).reshape(-1),
        sample_rate=sample_rate,
        duration_seconds=float(np.asarray(samples).shape[0]) / float(sample_rate or 1),
    )
    if audio.num_samples == 0:
        raise EmptyFileError("当前还没有可识别的音频数据。")
    return _run_inference(audio, lang), audio.duration_seconds
