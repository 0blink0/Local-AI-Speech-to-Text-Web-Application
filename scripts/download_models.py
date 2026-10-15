"""预下载（初始化）本地 ASR 模型权重。

题目 §F3 允许在首次安装/初始化时通过互联网下载权重，之后必须能离线运行。
本脚本把权重下载到项目内的 ``models/<size>/``，之后识别完全在本地完成。

**来源固定为 ModelScope（国内镜像，唯一来源）**：本项目面向国内网络环境，
官方 HuggingFace 域名实测不可达（连接被重置），镜像 CDN 亦仅 25 KB/s，
故不提供其它来源与回退，保证行为一致。实测下载速度约 6 MB/s。

用法::

    # 下载默认模型（base）到 models/base/
    python scripts/download_models.py

    # 指定规模与目录
    python scripts/download_models.py --model small
    python scripts/download_models.py --model base --dir D:\\models

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

from app.config import MODELSCOPE_ENDPOINT  # noqa: E402

MODELSCOPE_REPO = "Systran/faster-whisper-{size}"
MODELSCOPE_FILES_API = MODELSCOPE_ENDPOINT + "/api/v1/models/{repo}/repo/files?Revision=master"
MODELSCOPE_FILE_API = MODELSCOPE_ENDPOINT + "/api/v1/models/{repo}/repo?Revision=master&FilePath={path}"

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
                    print(
                        f"\r  {label}: {percent:5.1f}%  "
                        f"({downloaded / 1e6:.1f}/{total / 1e6:.1f} MB)",
                        end="",
                    )
        print(f"\r  {label}: 完成 ({downloaded / 1e6:.1f} MB)          ")


def download_model(model_size: str, target: Path) -> bool:
    """从 ModelScope 下载 CTranslate2 权重。"""
    repo = MODELSCOPE_REPO.format(size=model_size)
    print(f"数据源   : ModelScope（{repo}）")
    try:
        with urllib.request.urlopen(
            MODELSCOPE_FILES_API.format(repo=repo), timeout=30
        ) as response:
            payload = json.load(response)
    except Exception as exc:
        print(f"读取文件列表失败：{exc}")
        print("提示：请检查网络是否可访问 " + MODELSCOPE_ENDPOINT)
        return False

    files = payload.get("Data", {}).get("Files", [])
    if not files:
        print(f"文件列表为空，仓库 {repo} 可能不存在或不可访问。")
        return False

    for entry in files:
        name = entry.get("Path") or entry.get("Name")
        if not name or name in _SKIP_FILES:
            continue
        url = MODELSCOPE_FILE_API.format(repo=repo, path=urllib.parse.quote(name))
        _download(url, target / name, name)

    return True


def verify(target: Path) -> bool:
    """检查必需文件是否齐全，并做一次真实加载自检。"""
    missing = [name for name in _REQUIRED_FILES if not (target / name).exists()]
    if missing:
        print(f"错误：缺少必需文件 {missing}（目录：{target}）")
        return False

    try:
        import numpy as np
        from faster_whisper import WhisperModel

        model = WhisperModel(str(target), device="cpu", compute_type="int8")
        segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language="zh")
        list(segments)
    except Exception as exc:
        print(f"警告：模型加载或自检推理失败：{exc}")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="下载本地 ASR 模型权重（来源：ModelScope）")
    parser.add_argument(
        "--model",
        default=os.getenv("ASR_MODEL", "base"),
        help="模型规模：tiny/base/small/medium/large-v3（默认取 ASR_MODEL 或 base）",
    )
    parser.add_argument(
        "--dir",
        default=os.getenv("ASR_MODEL_DIR", str(PROJECT_ROOT / "models")),
        help="权重保存根目录，默认取 ASR_MODEL_DIR 或项目内 models/",
    )
    args = parser.parse_args()

    root = Path(args.dir).expanduser()
    target = root / args.model

    print("=" * 70)
    print(f"模型规模 : {args.model}")
    print(f"保存目录 : {target}")
    print(f"来源     : {MODELSCOPE_ENDPOINT}（唯一来源）")
    print("=" * 70)

    target.mkdir(parents=True, exist_ok=True)
    try:
        ok = download_model(args.model, target)
    except Exception as exc:
        print(f"下载失败：{exc}")
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
