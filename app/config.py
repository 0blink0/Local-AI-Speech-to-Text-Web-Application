"""应用配置。

所有可调项均可用环境变量覆盖，便于在当前机器与评委机器上以不同规模运行，
而不需要改代码。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """运行期配置。"""

    # 路径
    base_dir: Path = BASE_DIR
    static_dir: Path = BASE_DIR / "static"

    # ASR 引擎：mock | faster_whisper
    engine: str = "faster_whisper"
    # 模型规模或本地模型目录（tiny/base/small/medium/large-v3 或绝对路径）
    model_size: str = "small"
    # 权重下载/缓存目录；默认项目内 models/，便于离线复现
    model_dir: Path = BASE_DIR / "models"
    device: str = "cpu"
    compute_type: str = "int8"
    beam_size: int = 1
    # 是否允许自动检测语言（中英混合加分项）
    auto_detect_language: bool = False
    # 初始提示词：可偏置输出风格（如中文简体）或注入领域热词。
    # 默认留空：实测在短音频上可能诱发幻觉，故只作为可选项提供。
    initial_prompt_zh: str = ""
    initial_prompt_en: str = ""
    # 启动时预热模型（首次会触发权重下载）
    warmup: bool = True

    # 音频处理
    target_sample_rate: int = 16000
    max_upload_mb: int = 10
    max_record_seconds: int = 60
    # 支持的文件扩展名（用于提前给出友好提示；实际以内容嗅探为准）
    allowed_extensions: tuple[str, ...] = (
        ".wav",
        ".webm",
        ".ogg",
        ".oga",
        ".opus",
        ".mp3",
        ".flac",
        ".m4a",
        ".mp4",
        ".aac",
    )

    extra: dict = field(default_factory=dict)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def mock_engine(self) -> bool:
        return self.engine.lower() in {"mock", "test"}

    @classmethod
    def from_env(cls) -> "Settings":
        raw_dir = os.getenv("ASR_MODEL_DIR", "").strip()
        model_dir = Path(raw_dir).expanduser() if raw_dir else BASE_DIR / "models"
        return cls(
            static_dir=Path(_env_str("STATIC_DIR", str(BASE_DIR / "static"))),
            engine=_env_str("ASR_ENGINE", "faster_whisper"),
            model_size=_env_str("ASR_MODEL", "small"),
            model_dir=model_dir,
            device=_env_str("ASR_DEVICE", "cpu"),
            compute_type=_env_str("ASR_COMPUTE_TYPE", "int8"),
            beam_size=_env_int("ASR_BEAM_SIZE", 1),
            auto_detect_language=_env_bool("ASR_AUTO_DETECT_LANGUAGE", False),
            initial_prompt_zh=_env_str("ASR_INITIAL_PROMPT_ZH", ""),
            initial_prompt_en=_env_str("ASR_INITIAL_PROMPT_EN", ""),
            warmup=_env_bool("ASR_WARMUP", True),
            target_sample_rate=_env_int("TARGET_SAMPLE_RATE", 16000),
            max_upload_mb=_env_int("MAX_UPLOAD_MB", 10),
            max_record_seconds=_env_int("MAX_RECORD_SECONDS", 60),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例配置。测试中可用 ``get_settings.cache_clear()`` 重置。"""
    return Settings.from_env()
