"""HTTP 接口。

严格实现题目 §4 要求的三个接口：

* ``GET  /health``          —— 健康检查
* ``POST /api/transcribe``  —— 上传音频并识别
* ``GET  /``                —— Web 页面

另附一个非必做的 ``GET /api/info``，用于快速确认当前引擎/模型状态。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from app.asr.registry import get_engine, load_error
from app.config import Settings, get_settings
from app.core.languages import SUPPORTED_LANGUAGES
from app.errors import EmptyFileError, MissingFileError
from app.schemas import (
    EngineInfo,
    ErrorResponse,
    HealthResponse,
    TranscribeResponse,
)
from app.services.transcribe import transcribe_bytes

logger = logging.getLogger(__name__)

router = APIRouter()

_READ_CHUNK = 1024 * 1024

# 需要按文件修改时间打版本号的静态资源
_VERSIONED_ASSETS = ("style.css", "app.js", "pcm-worklet.js")


def _asset_version(path: Path) -> str:
    """用文件修改时间戳作为版本号；文件不存在时返回空串。"""
    try:
        return str(int(path.stat().st_mtime))
    except OSError:  # pragma: no cover - 交付物中必然存在
        return ""


def render_index(settings: Settings) -> str:
    """读取首页并给静态资源 URL 加上版本号后返回。

    背景：浏览器会无条件复用同 URL 的缓存资源，改完前端后用户按普通刷新
    仍可能看到旧样式（实测遇到"服务端已是新 CSS，页面还是旧布局"）。
    把 URL 变成 ``/static/style.css?v=<mtime>`` 后，文件一改 URL 就变，
    缓存自然失效，无需用户手动清缓存。
    """
    index_path = settings.static_dir / "index.html"
    html = index_path.read_text(encoding="utf-8")
    for name in _VERSIONED_ASSETS:
        version = _asset_version(settings.static_dir / name)
        if version:
            html = html.replace(f"/static/{name}", f"/static/{name}?v={version}")
    return html



async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    """按块读取上传内容，超过 ``limit`` 立即中止，避免大文件占满内存。"""
    from app.errors import FileTooLargeError

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise FileTooLargeError(
                f"音频文件超过 {limit / 1024 / 1024:.0f} MB 上限，请压缩或截短后重试。"
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """健康检查。按题目要求返回 ``{"status": "ok"}``。"""
    return HealthResponse(status="ok")


@router.get("/api/info", response_model=EngineInfo, tags=["system"])
async def info(settings: Settings = Depends(get_settings)) -> EngineInfo:
    """当前运行配置与模型状态（非必做接口，便于排查环境问题）。"""
    engine = get_engine()
    described = engine.describe()
    return EngineInfo(
        engine=str(described.get("engine", settings.engine)),
        model=str(described.get("model", settings.model_size)),
        device=str(described.get("device", settings.device)),
        compute_type=str(described.get("compute_type", settings.compute_type)),
        model_loaded=bool(described.get("loaded", engine.is_loaded)),
        load_error=load_error(),
        sample_rate=settings.target_sample_rate,
        max_upload_mb=settings.max_upload_mb,
        max_record_seconds=settings.max_record_seconds,
        hf_endpoint=settings.hf_endpoint,
        pypi_mirror=settings.pypi_mirror,
        supported_languages=list(SUPPORTED_LANGUAGES),
    )


@router.post(
    "/api/transcribe",
    response_model=TranscribeResponse,
    responses={
        400: {"model": ErrorResponse, "description": "参数或音频内容非法"},
        413: {"model": ErrorResponse, "description": "文件过大"},
        415: {"model": ErrorResponse, "description": "音频格式不支持"},
        500: {"model": ErrorResponse, "description": "推理失败"},
        503: {"model": ErrorResponse, "description": "模型不可用"},
    },
    tags=["asr"],
)
async def transcribe(
    file: UploadFile | None = File(default=None, description="音频文件"),
    language: str | None = Form(default=None, description="识别语言：zh 或 en"),
    settings: Settings = Depends(get_settings),
) -> TranscribeResponse:
    """上传一段音频并识别。

    请求为 ``multipart/form-data``，字段为 ``file`` 与 ``language``。
    """
    if file is None or (file.filename is None and file.size in (None, 0)):
        raise MissingFileError()

    data = await _read_limited(file, settings.max_upload_bytes)
    if not data:
        raise EmptyFileError()

    outcome = await run_in_threadpool(
        transcribe_bytes, data, language, source_name=file.filename, settings=settings
    )
    return TranscribeResponse(
        text=outcome.text,
        language=outcome.language,
        duration_seconds=outcome.duration_seconds,
        processing_seconds=outcome.processing_seconds,
    )


@router.get("/", include_in_schema=False, response_class=HTMLResponse)
async def index(settings: Settings = Depends(get_settings)) -> HTMLResponse:
    """返回 Web 页面（静态资源带版本号，避免浏览器用旧缓存）。"""
    return HTMLResponse(render_index(settings))
