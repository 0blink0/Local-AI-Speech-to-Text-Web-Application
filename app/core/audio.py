"""音频解码层。

职责：把任意上传/录音得到的字节流解码为 **16 kHz 单声道 float32** 的
numpy 数组，并计算真实时长。上层 ASR 引擎只依赖这个统一表示。

解码策略（按优先级）：

1. **PyAV**（wheel 内置 FFmpeg）：覆盖 WAV / WebM-Opus / OGG / MP3 / FLAC / M4A。
   这是浏览器 ``MediaRecorder`` 产出 ``audio/webm;codecs=opus`` 的关键。
2. **标准库 wave**：PyAV 不可用或解析失败时，对纯 PCM WAV 的兜底路径，
   保证"至少支持 WAV"这一硬性要求不依赖第三方库。
"""

from __future__ import annotations

import io
import logging
import wave
from dataclasses import dataclass

import numpy as np

from app.errors import (
    DecodeFailedError,
    EmptyFileError,
    FileTooLargeError,
    UnsupportedFormatError,
)

logger = logging.getLogger(__name__)

# 常见容器/编码的魔数指纹。仅用于给出更准确的错误分类，
# 真正的解码能力仍以 PyAV 能否解出音频帧为准。
_CONTAINER_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"RIFF", "wav"),
    (b"\x1aE\xdf\xa3", "webm"),  # EBML：WebM / MKV
    (b"OggS", "ogg"),
    (b"fLaC", "flac"),
    (b"ID3", "mp3"),
    (b"#!AMR", "amr"),
)

# 已知但不在支持范围内的容器：命中时直接 415，而不是含糊的"解码失败"
_KNOWN_UNSUPPORTED: tuple[str, ...] = ("amr",)


@dataclass(frozen=True)
class AudioData:
    """解码后的音频。"""

    samples: np.ndarray  # float32, shape=(n,), 单声道, 值域 [-1, 1]
    sample_rate: int
    duration_seconds: float
    container: str = "unknown"
    original_sample_rate: int = 0
    original_channels: int = 0

    @property
    def num_samples(self) -> int:
        return int(self.samples.shape[0])

    @property
    def is_silent(self) -> bool:
        return self.num_samples == 0 or float(np.max(np.abs(self.samples))) < 1e-4


def sniff_container(data: bytes) -> str:
    """根据文件头猜测容器格式，仅用于错误分类与日志。"""
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    for magic, name in _CONTAINER_MAGIC:
        if data.startswith(magic):
            return name
    # ISO-BMFF：offset 4 起为 'ftyp'（m4a/mp4）
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "mp4"
    return "unknown"


def _to_mono_float32(interleaved: np.ndarray, channels: int) -> np.ndarray:
    if channels > 1:
        interleaved = interleaved.reshape(-1, channels).mean(axis=1)
    return np.ascontiguousarray(interleaved, dtype=np.float32)


def _resample_linear(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """线性插值重采样。

    仅作为标准库兜底路径使用；主路径由 PyAV 的 SoXR/FFmpeg 重采样器完成，
    音质更好。对 ASR 而言线性插值的误差在可接受范围内。
    """
    if src_rate == dst_rate or samples.size == 0:
        return samples.astype(np.float32, copy=False)

    duration = samples.shape[0] / float(src_rate)
    target_length = max(1, int(round(duration * dst_rate)))
    src_positions = np.linspace(0.0, duration, num=samples.shape[0], endpoint=False)
    dst_positions = np.linspace(0.0, duration, num=target_length, endpoint=False)
    resampled = np.interp(dst_positions, src_positions, samples.astype(np.float64))
    return resampled.astype(np.float32)


def _decode_with_pyav(data: bytes, target_sr: int) -> tuple[np.ndarray, int, int] | None:
    """用 PyAV 解码并重采样为单声道 ``target_sr``。

    Returns:
        ``(samples, original_sample_rate, original_channels)``；
        PyAV 不可用或文件中没有音频流时返回 ``None``。
    """
    try:
        import av  # 延迟导入：缺少 PyAV 时仍可退化为纯 WAV 模式
    except Exception:  # pragma: no cover - 取决于环境
        logger.warning("PyAV 不可用，退化为标准库 WAV 解码路径")
        return None

    try:
        with av.open(io.BytesIO(data)) as container:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                return None

            original_rate = int(stream.rate or 0)
            original_channels = int(stream.channels or 0)

            resampler = av.audio.resampler.AudioResampler(
                format="flt", layout="mono", rate=target_sr
            )
            chunks: list[np.ndarray] = []

            def _collect(frames) -> None:
                if frames is None:
                    return
                if not isinstance(frames, (list, tuple)):
                    frames = [frames]
                for frame in frames:
                    arr = frame.to_ndarray()
                    if arr.size:
                        chunks.append(np.asarray(arr, dtype=np.float32).reshape(-1))

            for frame in container.decode(stream):
                _collect(resampler.resample(frame))
            _collect(resampler.resample(None))  # flush 尾部缓冲

        if not chunks:
            return None
        samples = np.concatenate(chunks).astype(np.float32, copy=False)
        if samples.size == 0:
            return None
        return samples, original_rate, original_channels
    except Exception as exc:  # 交给上层决定是 400 还是 415
        logger.debug("PyAV 解码失败: %s", exc)
        return None


def _decode_wav_stdlib(
    data: bytes, target_sr: int
) -> tuple[np.ndarray, int, int] | None:
    """标准库 WAV 解码（纯 PCM），作为 PyAV 的兜底。"""
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            if wf.getcomptype() != "NONE":
                return None  # 压缩 WAV（如 ADPCM）交给 PyAV
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            rate = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
    except Exception as exc:
        logger.debug("标准库 WAV 解码失败: %s", exc)
        return None

    if not raw or sampwidth not in (1, 2, 3, 4):
        return None

    dtype_map = {1: np.uint8, 2: np.int16, 3: None, 4: np.int32}
    if sampwidth == 3:  # 24-bit 需要手工拼装
        buf = np.frombuffer(raw, dtype=np.uint8)
        usable = (buf.size // 3) * 3
        buf = buf[:usable].reshape(-1, 3).astype(np.int32)
        values = (buf[:, 0] | (buf[:, 1] << 8) | (buf[:, 2] << 16)).astype(np.int32)
        values = np.where(values >= 0x800000, values - 0x1000000, values)
        samples = values.astype(np.float32) / 8388608.0
    else:
        dtype = dtype_map[sampwidth]
        values = np.frombuffer(raw, dtype=dtype)
        if sampwidth == 1:  # 8-bit WAV 为无符号
            samples = (values.astype(np.float32) - 128.0) / 128.0
        else:
            peak = float(1 << (8 * sampwidth - 1))
            samples = values.astype(np.float32) / peak

    samples = _to_mono_float32(samples, channels)
    samples = _resample_linear(samples, rate, target_sr)
    return samples.astype(np.float32, copy=False), rate, channels


def decode_audio(
    data: bytes,
    *,
    target_sr: int = 16000,
    max_bytes: int | None = None,
    source_name: str | None = None,
) -> AudioData:
    """把音频字节流解码为统一的 16 kHz 单声道 float32 表示。

    Args:
        data: 原始文件字节。
        target_sr: 目标采样率，默认 16 kHz（Whisper 期望值）。
        max_bytes: 允许的最大字节数，超出抛 ``FileTooLargeError``。
        source_name: 原始文件名，仅用于错误信息与日志。

    Raises:
        EmptyFileError: 文件为空。
        FileTooLargeError: 文件超出大小限制。
        UnsupportedFormatError: 识别为已知但不支持的容器。
        DecodeFailedError: 容器受支持但无法解出有效音频。
    """
    if not data:
        raise EmptyFileError()
    if max_bytes is not None and len(data) > max_bytes:
        raise FileTooLargeError(
            f"音频文件大小 {len(data) / 1024 / 1024:.1f} MB，"
            f"超出 {max_bytes / 1024 / 1024:.0f} MB 的限制。"
        )

    container = sniff_container(data)
    if container in _KNOWN_UNSUPPORTED:
        raise UnsupportedFormatError(
            f"暂不支持 {container.upper()} 格式的音频，请转换为 WAV 后重试。"
        )

    decoded = _decode_with_pyav(data, target_sr)
    decoder_used = "pyav"
    if decoded is None:
        decoded = _decode_wav_stdlib(data, target_sr)
        decoder_used = "wave"

    if decoded is None:
        if container in {"unknown", "mp4"}:
            # 既不是已知音频容器、两种解码器也都失败 → 大概率不是音频文件
            raise UnsupportedFormatError(
                f"无法识别的音频格式{f'（{source_name}）' if source_name else ''}，"
                "请上传 WAV 或浏览器录音生成的 WebM/OGG 文件。"
            )
        raise DecodeFailedError(
            f"音频内容无法解码{f'（{source_name}）' if source_name else ''}，"
            "文件可能已损坏或只包含视频流。"
        )

    samples, original_rate, original_channels = decoded
    if samples.size == 0:
        raise DecodeFailedError("音频中未解码出任何采样点，请确认文件内容有效。")

    duration = float(samples.shape[0]) / float(target_sr)
    logger.debug(
        "解码完成 decoder=%s container=%s rate=%s->%s channels=%s duration=%.2fs",
        decoder_used,
        container,
        original_rate,
        target_sr,
        original_channels,
        duration,
    )
    return AudioData(
        samples=samples,
        sample_rate=target_sr,
        duration_seconds=duration,
        container=container,
        original_sample_rate=original_rate,
        original_channels=original_channels,
    )


def encode_wav(samples: np.ndarray, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """把 float32 数组编码为 16-bit PCM WAV 字节流。

    Args:
        samples: 单声道时为 ``(n,)``；多声道时为交错排列的 ``(n * channels,)``。
        sample_rate: 采样率。
        channels: 声道数，默认单声道。

    ``channels`` 参数用于生成测试用立体声样本，以及模拟"用户提供的
    44.1 kHz 立体声录音"。实际服务端始终输出 16 kHz 单声道。
    """
    data = np.asarray(samples, dtype=np.float32).reshape(-1)
    clipped = np.clip(data, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(int(channels))
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm.tobytes())
    return buffer.getvalue()
