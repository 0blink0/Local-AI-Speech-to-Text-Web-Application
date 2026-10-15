"""WebSocket 近实时识别接口（加分项 F6）。

协议
----

客户端 → 服务端::

    {"type": "start", "language": "zh"}     # 文本控制帧，开始一次会话
    <binary>                                 # 16 kHz / 单声道 / int16 LE PCM 分片
    {"type": "stop"}                         # 文本控制帧，结束并取最终结果

服务端 → 客户端::

    {"type": "ready",  "sample_rate": 16000}
    {"type": "partial","text": "...", "duration_seconds": 2.4}
    {"type": "final",  "text": "...", "duration_seconds": 6.1, "processing_seconds": 1.2}
    {"type": "error",  "code": "MODEL_UNAVAILABLE", "message": "..."}

实现方式为"分片 + 滑动窗口重识别"：Whisper 并非原生流式模型，但对累积音频
定期重识别即可获得稳定的近实时效果。题目 §3 明确允许这种做法。
"""

from __future__ import annotations

import logging

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.core.languages import normalize_language
from app.errors import AppError
from app.services.transcribe import transcribe_samples

logger = logging.getLogger(__name__)

router = APIRouter()

# 每次触发重识别所需的最少新增音频（秒）
_MIN_NEW_AUDIO_SECONDS = 0.8
# 单会话缓冲上限（秒），超过则丢弃最旧音频，防止内存无界增长
_MAX_BUFFER_SECONDS = 120
# 触发一次识别的整体最小音频长度（秒）
_MIN_TOTAL_SECONDS = 0.4


def _pcm16_to_float32(payload: bytes) -> np.ndarray:
    """int16 little-endian PCM → float32 [-1, 1]。"""
    if len(payload) % 2:  # 丢掉不完整的半个采样点
        payload = payload[:-1]
    if not payload:
        return np.empty(0, dtype=np.float32)
    return (np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768.0).copy()


class _Session:
    """一次流式识别的状态。"""

    def __init__(self, language: str) -> None:
        self.language = language
        self.buffer = np.empty(0, dtype=np.float32)
        self.recognized_samples = 0  # 上次识别时缓冲区的长度

    @property
    def duration(self) -> float:
        return float(self.buffer.shape[0]) / 16000.0

    def append(self, pcm: np.ndarray) -> None:
        if pcm.size:
            self.buffer = np.concatenate((self.buffer, pcm))
        max_samples = _MAX_BUFFER_SECONDS * 16000
        if self.buffer.shape[0] > max_samples:
            self.buffer = self.buffer[-max_samples:]

    def new_audio_seconds(self) -> float:
        return (self.buffer.shape[0] - self.recognized_samples) / 16000.0

    def mark_recognized(self) -> None:
        self.recognized_samples = int(self.buffer.shape[0])

    @property
    def has_speech(self) -> bool:
        return self.duration >= _MIN_TOTAL_SECONDS


async def _send_error(websocket: WebSocket, exc: Exception) -> None:
    if isinstance(exc, AppError):
        payload = {"type": "error", "code": exc.code, "message": exc.message}
    else:
        payload = {"type": "error", "code": "INTERNAL_ERROR", "message": str(exc)}
    try:
        await websocket.send_json(payload)
    except Exception:  # 连接已断开
        logger.debug("发送错误消息失败，连接可能已关闭")


@router.websocket("/ws/stream")
async def stream(websocket: WebSocket) -> None:
    """近实时识别：边说话边返回已识别文本。"""
    await websocket.accept()
    sample_rate = get_settings().target_sample_rate
    session: _Session | None = None

    try:
        await websocket.send_json({"type": "ready", "sample_rate": sample_rate})

        while True:
            message = await websocket.receive()

            if message.get("type") == "websocket.disconnect":
                break

            text_payload = message.get("text")
            binary_payload = message.get("bytes")

            # ---------------- 文本控制帧 ----------------
            if text_payload is not None:
                import json

                try:
                    control = json.loads(text_payload)
                except json.JSONDecodeError:
                    await websocket.send_json(
                        {"type": "error", "code": "BAD_MESSAGE", "message": "控制帧不是合法 JSON"}
                    )
                    continue

                action = control.get("type")
                if action == "start":
                    try:
                        language = normalize_language(control.get("language"))
                    except AppError as exc:
                        await _send_error(websocket, exc)
                        continue
                    session = _Session(language)
                    await websocket.send_json(
                        {"type": "started", "language": language, "sample_rate": sample_rate}
                    )
                elif action == "stop":
                    if session is not None and session.has_speech:
                        import time

                        started = time.perf_counter()
                        text, duration = await run_in_threadpool(
                            transcribe_samples, session.buffer, sample_rate, session.language
                        )
                        await websocket.send_json(
                            {
                                "type": "final",
                                "text": text,
                                "duration_seconds": round(duration, 3),
                                "processing_seconds": round(time.perf_counter() - started, 3),
                            }
                        )
                    else:
                        await websocket.send_json(
                            {"type": "final", "text": "", "duration_seconds": 0.0,
                             "processing_seconds": 0.0}
                        )
                    session = None
                else:
                    await websocket.send_json(
                        {"type": "error", "code": "UNKNOWN_ACTION",
                         "message": f"未知控制指令 {action!r}"}
                    )
                continue

            # ---------------- 音频二进制帧 ----------------
            if binary_payload is not None:
                if session is None:
                    await websocket.send_json(
                        {"type": "error", "code": "NOT_STARTED",
                         "message": "请先发送 {'type':'start','language':'zh'} 开始会话"}
                    )
                    continue

                session.append(_pcm16_to_float32(binary_payload))

                if (
                    session.has_speech
                    and session.new_audio_seconds() >= _MIN_NEW_AUDIO_SECONDS
                ):
                    try:
                        text, duration = await run_in_threadpool(
                            transcribe_samples, session.buffer, sample_rate, session.language
                        )
                    except AppError as exc:
                        await _send_error(websocket, exc)
                        session = None
                        continue
                    session.mark_recognized()
                    await websocket.send_json(
                        {
                            "type": "partial",
                            "text": text,
                            "duration_seconds": round(duration, 3),
                        }
                    )

    except WebSocketDisconnect:
        logger.debug("流式连接已断开")
    except AppError as exc:
        await _send_error(websocket, exc)
    except Exception as exc:  # pragma: no cover - 防御性兜底
        logger.exception("流式识别异常")
        await _send_error(websocket, exc)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
