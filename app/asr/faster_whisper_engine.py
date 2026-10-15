"""基于 faster-whisper（CTranslate2）的本地 ASR 引擎。

选型理由（详见 ``docs/design.md``）：

* 单一模型同时覆盖中文普通话与英语，符合 F3 与"中英混合自动检测"加分项；
* CTranslate2 提供官方 Windows wheel，``pip`` 即可安装，无需自行编译；
* 支持 CPU int8 量化推理，普通开发机可用；
* 权重可在首次初始化时联网下载，之后完全离线运行。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

from app.asr.base import ASREngine, ASRResult, ASRSegment

logger = logging.getLogger(__name__)

# Whisper 只接受 16 kHz；引擎内部保证一致
_EXPECTED_SAMPLE_RATE = 16000


class FasterWhisperEngine(ASREngine):
    """CPU 优先的 faster-whisper 封装。"""

    name = "faster_whisper"

    def __init__(
        self,
        model_size: str = "small",
        *,
        device: str = "cpu",
        compute_type: str = "int8",
        download_root: Path | str | None = None,
        beam_size: int = 1,
        vad_filter: bool = True,
        local_files_only: bool = False,
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.download_root = str(download_root) if download_root else None
        self.beam_size = max(1, int(beam_size))
        self.vad_filter = vad_filter
        self.local_files_only = local_files_only

        self._model = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ 生命周期
    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """加载（必要时先下载）模型权重。幂等，线程安全。"""
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            from faster_whisper import WhisperModel  # 延迟导入，缩短启动时间

            if self.download_root:
                Path(self.download_root).mkdir(parents=True, exist_ok=True)

            logger.info(
                "正在加载 ASR 模型 model=%s device=%s compute_type=%s download_root=%s",
                self.model_size,
                self.device,
                self.compute_type,
                self.download_root,
            )
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                download_root=self.download_root,
                local_files_only=self.local_files_only,
            )
            logger.info("ASR 模型加载完成: %s", self.model_size)

    # ------------------------------------------------------------------ 识别
    def transcribe(
        self,
        samples: np.ndarray,
        sample_rate: int,
        language: str | None = None,
    ) -> ASRResult:
        self.load()
        assert self._model is not None  # load() 保证

        if sample_rate != _EXPECTED_SAMPLE_RATE:
            raise ValueError(
                f"faster-whisper 需要 {_EXPECTED_SAMPLE_RATE} Hz 输入，收到 {sample_rate} Hz"
            )

        audio = np.asarray(samples, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return ASRResult(text="", language=language or "auto")

        with self._lock:  # CTranslate2 推理不保证并发安全，串行化
            segments, info = self._model.transcribe(
                audio,
                language=language,
                beam_size=self.beam_size,
                vad_filter=self.vad_filter,
                vad_parameters={"min_silence_duration_ms": 500},
                condition_on_previous_text=False,
                without_timestamps=True,
            )
            collected: list[ASRSegment] = []
            pieces: list[str] = []
            for segment in segments:
                text = (segment.text or "").strip()
                if text:
                    pieces.append(text)
                collected.append(
                    ASRSegment(
                        start=float(getattr(segment, "start", 0.0) or 0.0),
                        end=float(getattr(segment, "end", 0.0) or 0.0),
                        text=text,
                    )
                )

        detected = getattr(info, "language", None) or language or "auto"
        text = " ".join(pieces).strip() if (language or "zh") == "en" else "".join(pieces)
        return ASRResult(
            text=text.strip(),
            language=str(detected),
            segments=tuple(collected),
        )

    def describe(self) -> dict[str, object]:
        info = super().describe()
        info.update(
            {
                "model": self.model_size,
                "device": self.device,
                "compute_type": self.compute_type,
            }
        )
        return info
