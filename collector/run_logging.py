from __future__ import annotations

import logging
import sys
from collections import deque
from logging.handlers import RotatingFileHandler

from collector.db import ROOT

LOG_PATH = ROOT / "logs" / "collection.log"
LOGGER_NAME = "jmm.collection"


def configure_collection_logging() -> logging.Logger:
    """Log collection progress to both the terminal/journal and a durable file."""
    logger = logging.getLogger(LOGGER_NAME)
    if getattr(logger, "_jmm_configured", False):
        return logger

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    file_handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger._jmm_configured = True  # type: ignore[attr-defined]
    return logger


def collection_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def read_collection_log_tail(lines: int = 500) -> str:
    if lines < 1:
        raise ValueError("lines must be >= 1")
    if not LOG_PATH.exists():
        return ""
    with LOG_PATH.open("r", encoding="utf-8", errors="replace") as handle:
        return "".join(deque(handle, maxlen=lines))
