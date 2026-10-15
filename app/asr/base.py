"""ASR 引擎抽象接口。

上层（``services/transcribe.py``）只依赖这里的接口，因此：

* 单元测试可以注入确定性的 ``MockEngine``，做到不联网、不下模型；
* 真实引擎可以替换（faster-whisper / 其他）而不影响 API 契约。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class ASRSegment:
    """带时间戳的识别片段。"""

    start: float
    end: float
    text: str


@dataclass(frozen=True)
class ASRResult:
    """一次识别的结果。"""

    text: str
    language: str
    segments: tuple[ASRSegment, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


class ASREngine(ABC):
    """语音识别引擎接口。

    约定：

    * ``transcribe`` 接收 **16 kHz 单声道 float32** 数组，值域 [-1, 1]。
    * ``language`` 为 ``"zh"`` / ``"en"``；传 ``None`` 表示交由引擎自动检测
      （用于中英混合加分项）。
    * 引擎内部异常应由调用方包装为 ``ModelUnavailableError`` /
      ``InferenceFailedError``，不要泄漏到底层。
    """

    name: str = "base"

    @abstractmethod
    def load(self) -> None:
        """加载模型权重。必须幂等，可被多次调用。"""

    @property
    @abstractmethod
    def is_loaded(self) -> bool:
        """模型是否已就绪。"""

    @abstractmethod
    def transcribe(
        self,
        samples: np.ndarray,
        sample_rate: int,
        language: str | None = None,
    ) -> ASRResult:
        """执行识别。"""

    def describe(self) -> dict[str, object]:
        """用于 ``/health`` 或日志的引擎信息。"""
        return {"engine": self.name, "loaded": self.is_loaded}
