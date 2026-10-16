"""FastAPI 应用入口。

统一启动方式（题目 §5.3）::

    python -m pip install -r requirements.txt
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import routes, ws
from app.asr import registry
from app.config import apply_network_source, get_settings
from app.core.logging import setup_logging
from app.errors import AppError

logger = logging.getLogger(__name__)

# HTTP 状态码 → 我方错误码，保证任何错误响应都是同一种结构
_STATUS_TO_CODE = {
    400: "BAD_REQUEST",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    413: "FILE_TOO_LARGE",
    415: "UNSUPPORTED_FORMAT",
    422: "VALIDATION_ERROR",
    500: "INTERNAL_ERROR",
    503: "MODEL_UNAVAILABLE",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时预热模型。

    预热失败**不会**阻止服务启动：识别请求会返回 503 并给出可读原因，
    这样"模型加载失败"场景下页面依然可用（题目 §F5）。
    """
    settings = get_settings()
    logger.info(
        "服务启动 engine=%s model=%s device=%s compute_type=%s",
        settings.engine,
        settings.model_size,
        settings.device,
        settings.compute_type,
    )
    registry.warmup()
    yield
    logger.info("服务已停止")


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("业务错误 %s: %s", exc.code, exc.message)
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求参数不合法，请检查 file 与 language 字段。",
                    "details": exc.errors(),
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": code,
                    "message": str(exc.detail)
                    if exc.detail
                    else "请求的资源不存在或不可用。",
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理异常")
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_ERROR", "message": f"服务器内部错误：{exc}"}},
        )


def create_app() -> FastAPI:
    """应用工厂。测试中可传入不同配置重建应用。"""
    setup_logging()
    # 固定网络来源（国内镜像），必须在任何 faster_whisper / huggingface_hub 导入之前
    apply_network_source()
    settings = get_settings()

    app = FastAPI(
        title="Local AI Speech-to-Text",
        description="基于本地开源 ASR 模型的中英文语音识别 Web 系统",
        version="1.0.0",
        lifespan=lifespan,
    )

    _register_exception_handlers(app)
    app.include_router(routes.router)
    app.include_router(ws.router)

    @app.middleware("http")
    async def _no_cache_static(request: Request, call_next):
        """页面与静态资源禁用浏览器缓存。

        本项目是本地开发/演示工具，改完前端往往直接刷新查看；若浏览器缓存了
        旧的 HTML/CSS/JS，会出现"改了没生效"的困惑。这里统一加 no-cache，
        让每次刷新都向服务端确认一次（内容未变仍走 304，开销可忽略）。
        """
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    if settings.static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")
    else:  # pragma: no cover - 正常交付必然存在
        logger.warning("静态目录不存在：%s", settings.static_dir)

    return app


app = create_app()
