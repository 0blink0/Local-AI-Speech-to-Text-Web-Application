"""对比不同模型规模的识别质量与 CPU 推理速度。

用途：为"默认模型规模"的选型提供可测量的依据（见 docs/test-report.md）。

用法::

    python scripts/benchmark_models.py                      # 对比 tiny/base/small（已下载的会跳过）
    python scripts/benchmark_models.py --models base small
    python scripts/benchmark_models.py --models small --repeat 3

输出：每个模型在 samples/ 下中英文样本上的识别文本、与 reference.txt 的相似度、
音频时长、处理耗时与 RTF（实时率 = 处理耗时 / 音频时长，越小越快）。
"""

from __future__ import annotations

import argparse
import difflib
import re
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

SAMPLES_DIR = PROJECT_ROOT / "samples"
TARGETS = {"zh": SAMPLES_DIR / "chinese.wav", "en": SAMPLES_DIR / "english.wav"}


def _normalize(text: str, language: str) -> str:
    text = text.strip().lower()
    if language == "zh":
        return re.sub(r"[^\w\u4e00-\u9fff]", "", text)
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", text).split())


def _similarity(actual: str, expected: str, language: str) -> float:
    a, b = _normalize(actual, language), _normalize(expected, language)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _read_references() -> dict[str, str]:
    path = SAMPLES_DIR / "reference.txt"
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or not line.startswith("["):
            continue
        key, _, value = line[1:].partition("]")
        out[key.strip().lower()] = value.strip()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="对比 ASR 模型规模的质量与速度")
    parser.add_argument("--models", nargs="+", default=["tiny", "base", "small"])
    parser.add_argument("--dir", default=str(PROJECT_ROOT / "models"))
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--repeat", type=int, default=1, help="每个样本重复次数，取耗时中位数")
    args = parser.parse_args()

    missing = [name for name, path in TARGETS.items() if not path.exists()]
    if missing:
        print("缺少测试音频：" + ", ".join(missing) + "（见 samples/README.md）")
        return 1
    references = _read_references()

    from faster_whisper import WhisperModel

    root = Path(args.dir)
    for size in args.models:
        model_path = root / size
        if not (model_path / "model.bin").exists():
            print(f"\n===== {size} ===== 跳过：未下载（python scripts/download_models.py --model {size}）")
            continue

        print(f"\n===== {size} =====")
        started = time.perf_counter()
        model = WhisperModel(str(model_path), device="cpu", compute_type=args.compute_type)
        print(f"加载耗时: {time.perf_counter() - started:.2f}s")

        for language, path in TARGETS.items():
            samples = _read_wav_float32(path)
            duration = samples.shape[0] / 16000.0
            times: list[float] = []
            text = ""
            for _ in range(max(1, args.repeat)):
                t0 = time.perf_counter()
                segments, _info = model.transcribe(
                    samples, language=language, beam_size=1, vad_filter=True,
                    condition_on_previous_text=False, without_timestamps=True,
                )
                text = "".join(s.text for s in segments).strip()
                times.append(time.perf_counter() - t0)
            elapsed = statistics.median(times)
            score = (
                _similarity(text, references[language], language)
                if references.get(language)
                else float("nan")
            )
            print(f"[{language}] 音频 {duration:.2f}s  处理 {elapsed:.2f}s  RTF {elapsed / duration:.3f}  相似度 {score:.3f}")
            print(f"      文本: {text}")

    print("\n提示：RTF < 1 表示快于实时。")
    return 0


def _read_wav_float32(path: Path):
    from app.core.audio import decode_audio

    return decode_audio(path.read_bytes(), target_sr=16000).samples


if __name__ == "__main__":
    raise SystemExit(main())
