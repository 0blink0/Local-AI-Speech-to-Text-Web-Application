"""API 的 Pydantic 数据模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """``GET /health`` 响应。按题目要求严格只返回 ``status``。"""

    status: str = Field(default="ok", examples=["ok"])


class ErrorDetail(BaseModel):
    code: str = Field(description="机器可读的错误码，例如 INVALID_LANGUAGE")
    message: str = Field(description="人类可读的错误说明，可直接展示给用户")


class ErrorResponse(BaseModel):
    error: ErrorDetail


class TranscribeResponse(BaseModel):
    """``POST /api/transcribe`` 成功响应。字段与题目 4.2 节一致。"""

    text: str = Field(description="识别得到的文本，无有效语音时为空字符串")
    language: str = Field(description="本次请求选择的识别语言：zh 或 en")
    duration_seconds: float = Field(description="音频实际时长（秒）")
    processing_seconds: float = Field(description="服务端处理耗时（秒），含解码与推理")


class EngineInfo(BaseModel):
    """``GET /api/info`` 响应（非必做接口，便于排查环境问题）。"""

    engine: str
    model: str
    device: str
    compute_type: str
    model_loaded: bool
    load_error: str | None = None
    sample_rate: int
    max_upload_mb: int
    max_record_seconds: int
    hf_endpoint: str
    pypi_mirror: str
    save_recordings: bool
    supported_languages: list[str]


class RecordingInfo(BaseModel):
    """一条已存档的录音（``GET /api/recordings``）。"""

    filename: str = Field(description="存档文件名")
    size_bytes: int = Field(description="文件大小（字节）")
    created_at: float = Field(description="保存时间（Unix 时间戳，秒）")
    url: str = Field(description="回放/下载地址")


class RecordingListResponse(BaseModel):
    recordings: list[RecordingInfo]
