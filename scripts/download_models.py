"""预下载（初始化）本地 ASR 模型权重。

题目 §F3 允许在首次安装/初始化时通过互联网下载权重，之后必须能离线运行。
本脚本把权重下载到项目内的 ``models/<size>/``，之后识别完全在本地完成。

用法::

    # HuggingFace（默认）
    python scripts/download_models.py
    python scripts/download_models.py --mirror          # 使用 hf-mirror.com 镜像
    python scripts/download_models.py --model base      # 更小的模型

    # ModelScope（国内网络推荐，速度通常更好）
    python scripts/download_models.py --source modelscope

下载完成后无需任何额外配置，应用会自动使用 ``models/<size>/`` 下的权重
（见 ``app/asr/registry.py::resolve_model_path``）。离线验证::

    set HF_HUB_OFFLINE=1
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

HF_MIRROR_ENDPOINT = "https://hf-mirror.com"

MODELSCOPE_REPO = "Systran/faster-whisper-{size}"
MODELSCOPE_FILES_API = "https://modelscope.cn/api/v1/models/{repo}/repo/files?Revision=master"
MODELSCOPE_FILE_API = "https://modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={path}"

# 这些文件对推理无用，不必下载
_SKIP_FILES = {".gitattributes", "README.md", "configuration.json"}
# faster-whisper 加载本地目录时必需的文件
_REQUIRED_FILES = ("model.bin", "config.json", "tokenizer.json")


def _download(url: str, destination: Path, label: str) -> None:
    """流式下载并显示进度；已存在且大小一致则跳过。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "local-stt-downloader/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        total = int(response.headers.get("Content-Length") or 0)
        if destination.exists() and total and destination.stat().st_size == total:
            print(f"  已存在，跳过：{label}")
            return
        downloaded = 0
        with destination.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if total:
                    percent = downloaded * 100.0 / total
                    print(f"\r  {label}: {percent:5.1f}%  ({downloaded / 1e6:.1f}/{total / 1e6:.1f} MB)", end="")
        print(f"\r  {label}: 完成 ({downloaded / 1e6:.1f} MB)          ")


def download_from_modelscope(model_size: str, target: Path) -> bool:
    """从 ModelScope 下载 CTranslate2 权重（国内网络更快）。"""
    repo = MODELSCOPE_REPO.format(size=model_size)
    print(f"数据源   : ModelScope ({repo})")
    try:
        with urllib.request.urlopen(MODELSCOPE_FILES_API.format(repo=repo), timeout=30) as response:
            payload = json.load(response)
    except Exception as exc:
        print(f"读取文件列表失败：{exc}")
        return False

    files = payload.get("Data", {}).get("Files", [])
    if not files:
        print("文件列表为空，仓库名可能不存在。")
        return False

    for entry in files:
        name = entry.get("Path") or entry.get("Name")
        if not name or name in _SKIP_FILES:
            continue
        url = MODELSCOPE_FILE_API.format(repo=repo, path=urllib.parse.quote(name))
        _download(url, target / name, name)

    return True


def download_from_hf(model_size: str, target: Path, mirror: bool) -> bool:
    """通过 huggingface_hub 下载（首次初始化最标准的做法）。"""
    if mirror:
        os.environ["HF_ENDPOINT"] = HF_MIRROR_ENDPOINT
        print(f"数据源   : HuggingFace（镜像 {HF_MIRROR_ENDPOINT}）")
    else:
        print("数据源   : HuggingFace" + (f"（{os.environ['HF_ENDPOINT']}）" if os.getenv("HF_ENDPOINT") else ""))

    from faster_whisper import WhisperModel

    WhisperModel(
        model_size,
        device="cpu",
        compute_type="int8",
        download_root=str(target.parent),
    )
    return True


def verify(target: Path) -> bool:
    """检查必需文件是否齐全，并做一次真实加载自检。"""
    missing = [name for name in _REQUIRED_FILES if not (target / name).exists()]
    if missing:
        print(f"错误：缺少必需文件 {missing}（目录：{target}）")
        return False

    try:
        from faster_whisper import WhisperModel

        import numpy as np

        model = WhisperModel(str(target), device="cpu", compute_type="int8")
        segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language="zh")
        list(segments)
    except Exception as exc:
        print(f"警告：模型加载或自检推理失败：{exc}")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="下载本地 ASR 模型权重")
    parser.add_argument(
        "--model",
        default=os.getenv("ASR_MODEL", "small"),
        help="模型规模：tiny/base/small/medium/large-v3（默认取 ASR_MODEL 或 small）",
    )
    parser.add_argument(
        "--dir",
        default=os.getenv("ASR_MODEL_DIR", str(PROJECT_ROOT / "models")),
        help="权重保存根目录，默认取 ASR_MODEL_DIR 或项目内 models/",
    )
    parser.add_argument(
        "--source",
        choices=["hf", "modelscope"],
        default="hf",
        help="下载源；国内网络建议 modelscope",
    )
    parser.add_argument("--mirror", action="store_true", help="HuggingFace 使用 hf-mirror.com 镜像")
    args = parser.parse_args()

    root = Path(args.dir).expanduser()
    target = root / args.model
    target.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print(f"模型规模 : {args.model}")
    print(f"保存目录 : {target}")
    print("=" * 70)

    try:
        if args.source == "modelscope":
            ok = download_from_modelscope(args.model, target)
        else:
            ok = download_from_hf(args.model, target, args.mirror)
    except ImportError:
        print("错误：未安装 faster-whisper。请先执行 python -m pip install -r requirements.txt")
        return 1
    except Exception as exc:
        print(f"下载失败：{exc}")
        if args.source == "hf":
            print("提示：网络受限时请改用 --mirror，或直接使用 ModelScope：")
            print("      python scripts/download_models.py --source modelscope")
        return 1

    if not ok:
        return 1

    print("-" * 70)
    print("正在校验模型可用性 …")
    if not verify(target):
        return 1

    print("-" * 70)
    print("完成。模型已就绪，后续识别无需联网。")
    print("启动服务：")
    print("  python -m uvicorn app.main:app --host 127.0.0.1 --port 8000")
    print("离线验证（推荐）：")
    print("  set HF_HUB_OFFLINE=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
