"""日志初始化。"""

from __future__ import annotations

import logging
import os


def setup_logging() -> None:
    """配置根 logger。重复调用无副作用。"""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    root.setLevel(level)
    # 降低第三方库噪音
    for noisy in ("faster_whisper", "ctranslate2", "huggingface_hub", "urllib3"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))
