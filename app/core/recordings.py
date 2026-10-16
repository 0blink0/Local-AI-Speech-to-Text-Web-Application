"""麦克风录音的服务端存档。

用途：把用户通过浏览器录制并提交的音频原样保存到本地目录，便于：
* 事后回听与核对识别结果；
* 排查"识别不准/解码失败"类问题时拿到原始输入。

设计要点：

* **只存档录音，不存档上传文件**——调用方仅在 ``source=recording`` 时触发，
  避免把用户上传的任意文件堆到磁盘上。
* **原样保存原始字节**：不做转码，保留浏览器产出的真实格式（WebM/Opus 等），
  这样归档文件就是"用户实际录到的东西"。
* **安全**：读取接口只接受安全文件名，并校验解析后的路径确实位于存档目录内，
  防止路径穿越。
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.core.audio import sniff_container

logger = logging.getLogger(__name__)

# 容器名 → 文件扩展名
_CONTAINER_SUFFIX: dict[str, str] = {
    "wav": ".wav",
    "webm": ".webm",
    "ogg": ".ogg",
    "mp4": ".m4a",
    "aac": ".aac",
    "mp3": ".mp3",
    "flac": ".flac",
}

# 只允许这类文件名，杜绝路径穿越与特殊字符
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")

# 只把音频文件视为录音。
# 存档目录是普通文件夹，用户可能往里放其它文件（实测有人放进过分析用的 .md）；
# 若不过滤，"最近一次录音"会被非音频文件顶掉，回放直接失败。
_AUDIO_SUFFIXES = frozenset(
    {".wav", ".webm", ".ogg", ".oga", ".opus", ".mp3", ".flac", ".m4a", ".mp4", ".aac", ".bin"}
)


@dataclass(frozen=True)
class SavedRecording:
    """一条已存档的录音。"""

    filename: str
    size_bytes: int
    created_at: float  # Unix 时间戳（秒）

    @property
    def url(self) -> str:
        return f"/api/recordings/{self.filename}"


def _suffix_for(data: bytes, fallback_name: str | None) -> str:
    """根据内容嗅探决定扩展名；失败时回退到上传文件名的后缀。"""
    container = sniff_container(data)
    if container in _CONTAINER_SUFFIX:
        return _CONTAINER_SUFFIX[container]
    if fallback_name:
        suffix = Path(fallback_name).suffix
        if re.fullmatch(r"\.[A-Za-z0-9]{1,5}", suffix or ""):
            return suffix.lower()
    return ".bin"


def build_filename(data: bytes, language: str, fallback_name: str | None) -> str:
    """生成形如 ``20261016-153012-zh-a1b2.webm`` 的文件名。

    包含时间戳（便于排序）、语言（便于筛选）与一小段随机串（避免同秒冲突）。
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    lang = re.sub(r"[^a-z]", "", (language or "unk").lower()) or "unk"
    token = secrets.token_hex(2)
    return f"{stamp}-{lang}-{token}{_suffix_for(data, fallback_name)}"


def save_recording(
    data: bytes,
    *,
    language: str,
    settings: Settings,
    fallback_name: str | None = None,
) -> SavedRecording | None:
    """把录音原样写入存档目录。

    Returns:
        成功时返回 ``SavedRecording``；存档被关闭或写入失败时返回 ``None``。
        存档失败**不应**影响识别主流程，因此这里只记日志不抛异常。
    """
    if not settings.save_recordings:
        return None

    directory = Path(settings.recordings_dir)
    filename = build_filename(data, language, fallback_name)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / filename
        target.write_bytes(data)
    except OSError as exc:
        logger.warning("录音存档失败（不影响识别结果）：%s", exc)
        return None

    logger.info("录音已存档 %s（%.1f KB）", filename, len(data) / 1024)
    return SavedRecording(
        filename=filename,
        size_bytes=len(data),
        created_at=target.stat().st_mtime,
    )


def list_recordings(settings: Settings, limit: int | None = None) -> list[SavedRecording]:
    """列出存档录音，按时间倒序（最新在前）。"""
    directory = Path(settings.recordings_dir)
    if not directory.is_dir():
        return []

    limit = settings.recordings_list_limit if limit is None else limit
    entries: list[SavedRecording] = []
    for path in directory.iterdir():
        try:
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.lower() not in _AUDIO_SUFFIXES:
                continue
            stat = path.stat()
        except OSError:  # pragma: no cover - 文件刚好被删除
            continue
        entries.append(
            SavedRecording(filename=path.name, size_bytes=stat.st_size, created_at=stat.st_mtime)
        )

    entries.sort(key=lambda item: item.created_at, reverse=True)
    return entries[: max(0, limit)]


def resolve_recording(filename: str, settings: Settings) -> Path | None:
    """把文件名解析为磁盘路径；非法名称或文件不存在时返回 ``None``。

    只接受 ``_SAFE_NAME`` 形式的文件名，并二次校验解析结果位于存档目录内。
    """
    if not _SAFE_NAME.match(filename or ""):
        return None
    if Path(filename).suffix.lower() not in _AUDIO_SUFFIXES:
        return None

    directory = Path(settings.recordings_dir).resolve()
    candidate = (directory / filename).resolve()
    # 二次校验：防止通过符号链接等方式逃出存档目录
    if candidate.parent != directory or not candidate.is_file():
        return None
    return candidate


def cleanup_hint(settings: Settings) -> str:
    """给日志/接口用的存档目录说明。"""
    return str(Path(settings.recordings_dir))


def now() -> float:  # pragma: no cover - 便于测试替换
    return time.time()
