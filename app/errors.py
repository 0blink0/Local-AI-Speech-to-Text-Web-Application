"""统一错误体系。

每个错误都携带 ``code``（机器可读）与 ``message``（人类可读），
由 ``main.py`` 注册的异常处理器统一序列化为::

    {"error": {"code": "...", "message": "..."}}

这样可以保证任何失败路径都返回结构化、可理解的信息，
而不会把堆栈直接暴露给前端。
"""

from __future__ import annotations


class AppError(Exception):
    """所有业务错误的基类。"""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    default_message: str = "服务器内部错误。"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        self.message = message or self.default_message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.message)

    def to_dict(self) -> dict[str, dict[str, str]]:
        return {"error": {"code": self.code, "message": self.message}}


# ---------------------------------------------------------------- 4xx：请求侧


class MissingFileError(AppError):
    code = "MISSING_FILE"
    status_code = 400
    default_message = "未收到音频文件，请选择或录制一段音频后再试。"


class MissingFieldError(AppError):
    code = "MISSING_FIELD"
    status_code = 400
    default_message = "请求缺少必要参数。"


class InvalidLanguageError(AppError):
    code = "INVALID_LANGUAGE"
    status_code = 400
    default_message = "language 参数无效，仅支持 'zh'（中文）或 'en'（英文）。"


class EmptyFileError(AppError):
    code = "EMPTY_FILE"
    status_code = 400
    default_message = "上传的音频文件为空，请重新选择文件。"


class DecodeFailedError(AppError):
    code = "DECODE_FAILED"
    status_code = 400
    default_message = "无法解码该音频文件，文件可能已损坏或内容不是有效音频。"


class UnsupportedFormatError(AppError):
    code = "UNSUPPORTED_FORMAT"
    status_code = 415
    default_message = "不支持该音频格式，请上传 WAV 或浏览器录音格式（WebM/OGG）。"


class FileTooLargeError(AppError):
    code = "FILE_TOO_LARGE"
    status_code = 413
    default_message = "音频文件过大，超出服务端限制。"


class AudioTooLongError(AppError):
    code = "AUDIO_TOO_LONG"
    status_code = 400
    default_message = "音频时长超出允许范围。"


# ---------------------------------------------------------------- 5xx：服务侧


class ModelUnavailableError(AppError):
    code = "MODEL_UNAVAILABLE"
    status_code = 503
    default_message = (
        "语音识别模型当前不可用（可能尚未下载或加载失败）。"
        "请先运行模型初始化脚本，或稍后重试。"
    )


class InferenceFailedError(AppError):
    code = "INFERENCE_FAILED"
    status_code = 500
    default_message = "语音识别推理过程出错，请重试。"
