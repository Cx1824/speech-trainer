"""日志配置。"""

from __future__ import annotations

import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=level.upper(),
        format=fmt,
        stream=sys.stdout,
        force=True,
    )
    # 降低第三方库噪声
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
