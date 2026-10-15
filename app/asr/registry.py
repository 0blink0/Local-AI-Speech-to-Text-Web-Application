"""引擎注册表：按配置构建引擎并做单例懒加载。

设计要点：

* **单例**：模型权重只加载一次，避免每个请求重复占用内存。
* **懒加载 + 预热**：启动时预热（失败不影响服务启动），首个请求也不会重复加载。
* **失败可诊断**：加载失败时缓存错误原因，后续请求返回 503 且带可理解信息，
  而不是每次请求都重新尝试（那会让首个请求长时间无响应）。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.asr.base import ASREngine
from app.config import Settings, get_settings
from app.errors import ModelUnavailableError

logger = logging.getLogger(__name__)

_engine: ASREngine | None = None
_engine_lock = threading.RLock()
_load_error: str | None = None


def resolve_model_path(settings: Settings) -> str:
    """决定传给 faster-whisper 的模型标识。

    优先级：

    1. ``ASR_MODEL`` 本身就是一个存在的目录 → 直接使用（本地目录形态的权重）。
    2. ``<ASR_MODEL_DIR>/<ASR_MODEL>/model.bin`` 存在 → 使用该本地目录，
       这样 ``scripts/download_models.py`` 下载后无需任何额外配置。
    3. 否则返回原始名称（如 ``small``），交由 faster-whisper 从 Hub 解析。
    """
    raw = (settings.model_size or "").strip()
    candidate = Path(raw)
    if candidate.is_dir():
        return str(candidate)

    local_dir = Path(settings.model_dir) / raw if settings.model_dir else None
    if local_dir and (local_dir / "model.bin").exists():
        return str(local_dir)

    return raw


def build_engine(settings: Settings) -> ASREngine:
    """根据配置创建引擎实例（不加载权重）。"""
    name = (settings.engine or "").strip().lower()

    if name in {"mock", "test"}:
        from app.asr.mock_engine import MockEngine

        return MockEngine()

    if name in {"faster_whisper", "faster-whisper", "whisper"}:
        from app.asr.faster_whisper_engine import FasterWhisperEngine

        return FasterWhisperEngine(
            model_size=resolve_model_path(settings),
            device=settings.device,
            compute_type=settings.compute_type,
            download_root=settings.model_dir,
            beam_size=settings.beam_size,
            initial_prompts={
                "zh": settings.initial_prompt_zh,
                "en": settings.initial_prompt_en,
            },
        )

    raise ModelUnavailableError(
        f"未知的 ASR 引擎配置 ASR_ENGINE={settings.engine!r}，"
        "可选值为 'faster_whisper' 或 'mock'。"
    )


def get_engine() -> ASREngine:
    """返回进程内唯一的引擎实例（可能尚未加载权重）。"""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = build_engine(get_settings())
    return _engine


def set_engine(engine: ASREngine | None) -> None:
    """显式注入引擎实例，供测试使用。"""
    global _engine, _load_error
    with _engine_lock:
        _engine = engine
        _load_error = None


def reset_engine() -> None:
    """清除单例与缓存的错误，供测试使用。"""
    set_engine(None)


def load_error() -> str | None:
    """返回最近一次模型加载失败的原因，未失败则为 ``None``。"""
    return _load_error


def ensure_ready() -> ASREngine:
    """确保引擎可用；必要时加载权重。

    Raises:
        ModelUnavailableError: 引擎构建或权重加载失败。
    """
    global _load_error
    try:
        engine = get_engine()
    except ModelUnavailableError:
        raise
    except Exception as exc:  # 构建阶段的意外错误
        _load_error = str(exc)
        raise ModelUnavailableError(f"初始化 ASR 引擎失败：{exc}") from exc

    if engine.is_loaded:
        return engine

    if _load_error is not None:
        # 之前已失败过：直接返回缓存原因，避免每次请求都长时间阻塞
        raise ModelUnavailableError(_load_error)

    with _engine_lock:
        if engine.is_loaded:
            return engine
        try:
            engine.load()
        except Exception as exc:
            _load_error = (
                f"加载模型失败：{exc}。"
                "请确认模型已初始化（见 README「模型初始化」一节），"
                "或在联网状态下重试。"
            )
            logger.exception("ASR 模型加载失败")
            raise ModelUnavailableError(_load_error) from exc

    _load_error = None
    return engine


def warmup() -> bool:
    """启动时预热。失败只记录日志，不阻断服务启动。"""
    settings = get_settings()
    if not settings.warmup:
        logger.info("已按配置跳过模型预热（ASR_WARMUP=0）")
        return False
    try:
        ensure_ready()
        logger.info("模型预热完成：%s", get_engine().describe())
        return True
    except Exception as exc:
        logger.warning("模型预热失败，服务仍会启动；识别请求将返回 503：%s", exc)
        return False
