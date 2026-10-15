"""把任意格式/采样率的录音统一转换为 ``samples/`` 要求的规格，并做校验。

规格（题目 §6.2）：16 kHz / 单声道 / 16-bit PCM WAV / 5–15 秒。

用法::

    # 转换 + 校验（支持 WAV/WebM/OGG/MP3/M4A/FLAC …）
    python scripts/prepare_samples.py --zh 原始录音.m4a --en raw/english.mp3

    # 只校验已有 samples/
    python scripts/prepare_samples.py --check

转换完成后请手工填写 ``samples/reference.txt``（人工参考文本）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402

from app.core.audio import decode_audio, encode_wav  # noqa: E402

SAMPLES_DIR = PROJECT_ROOT / "samples"
TARGETS = {"zh": SAMPLES_DIR / "chinese.wav", "en": SAMPLES_DIR / "english.wav"}

MIN_SECONDS = 5.0
MAX_SECONDS = 15.0
HARD_MAX_SECONDS = 60.0
MIN_PEAK = 0.01  # 峰值过低说明几乎没录到声音


def describe(samples: np.ndarray, sample_rate: int) -> str:
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
    return f"峰值={peak:.3f} RMS={rms:.4f}"


def convert(source: Path, destination: Path, language: str) -> bool:
    if not source.exists():
        print(f"[{language}] 源文件不存在：{source}")
        return False

    try:
        audio = decode_audio(source.read_bytes(), target_sr=16000, source_name=source.name)
    except Exception as exc:
        print(f"[{language}] 解码失败：{exc}")
        return False

    ok = True
    print(
        f"[{language}] 已解码 {source.name}："
        f"{audio.duration_seconds:.2f}s 原始={audio.original_sample_rate}Hz/"
        f"{audio.original_channels}声道 容器={audio.container} {describe(audio.samples, audio.sample_rate)}"
    )

    if audio.duration_seconds > HARD_MAX_SECONDS:
        print(f"[{language}] 错误：时长超过 {HARD_MAX_SECONDS:.0f} 秒，请截短后重试。")
        ok = False
    elif not (MIN_SECONDS <= audio.duration_seconds <= MAX_SECONDS):
        print(
            f"[{language}] 警告：建议时长 {MIN_SECONDS:.0f}–{MAX_SECONDS:.0f} 秒"
            f"（当前 {audio.duration_seconds:.2f} 秒），仍会写入但可能影响评测。"
        )

    if float(np.max(np.abs(audio.samples))) < MIN_PEAK:
        print(f"[{language}] 警告：音频几乎是静音，请确认麦克风音量。")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(encode_wav(audio.samples, 16000))
    print(f"[{language}] 已写入 {destination.relative_to(PROJECT_ROOT)}（16 kHz / 单声道 / PCM WAV）")
    return ok


def check() -> bool:
    ok = True
    for language, path in TARGETS.items():
        if not path.exists():
            print(f"[{language}] 缺失：{path.relative_to(PROJECT_ROOT)}")
            ok = False
            continue
        try:
            audio = decode_audio(path.read_bytes(), target_sr=16000)
        except Exception as exc:
            print(f"[{language}] 无法解码：{exc}")
            ok = False
            continue
        status = "OK"
        if not (MIN_SECONDS <= audio.duration_seconds <= MAX_SECONDS):
            status = "时长不在建议区间"
            ok = False
        if float(np.max(np.abs(audio.samples))) < MIN_PEAK:
            status = "疑似静音"
            ok = False
        print(
            f"[{language}] {path.name}: {audio.duration_seconds:.2f}s / "
            f"{audio.original_sample_rate or audio.sample_rate}Hz / "
            f"{audio.original_channels or 1}ch {describe(audio.samples, audio.sample_rate)} → {status}"
        )

    reference = SAMPLES_DIR / "reference.txt"
    if not reference.exists():
        print("缺失：samples/reference.txt（人工参考文本）")
        ok = False
    else:
        filled = [
            line
            for line in reference.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("[")
        ]
        print(f"reference.txt 已填写 {len(filled)} 条（需要 zh 与 en 各 1 条）")
        if len(filled) < 2:
            print("提示：请填写 [zh] 与 [en] 两条参考文本，真实模型测试会用它做相似度断言。")
            ok = False
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="统一并校验 samples/ 测试音频")
    parser.add_argument("--zh", help="中文原始录音路径（任意常见格式）")
    parser.add_argument("--en", help="英文原始录音路径（任意常见格式）")
    parser.add_argument("--check", action="store_true", help="只校验现有 samples/")
    args = parser.parse_args()

    if args.check or (not args.zh and not args.en):
        ok = check()
        print("\n校验" + ("通过。" if ok else "未通过，请按提示修正。"))
        return 0 if ok else 1

    ok = True
    if args.zh:
        ok &= convert(Path(args.zh), TARGETS["zh"], "zh")
    if args.en:
        ok &= convert(Path(args.en), TARGETS["en"], "en")

    print()
    if check():
        print("全部就绪。接下来可运行真实模型测试：python -m pytest -q -m real_model")
        return 0
    print("请补齐缺失项后重新运行 --check。")
    return 1 if not ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
