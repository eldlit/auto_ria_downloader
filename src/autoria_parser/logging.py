"""Logging helpers for the Autoria parser."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.logging import RichHandler

_LOG_SENTINEL = "_autoria_logging_configured"


def setup_logging(level: Optional[str] = None) -> None:
    """Configure console + file logging only once per process."""
    root = logging.getLogger()
    if getattr(root, _LOG_SENTINEL, False):
        return

    console_handler = RichHandler(rich_tracebacks=True, markup=False)
    fmt = "%(message)s"

    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = log_dir / f"run-{timestamp}.log"
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_format = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_format)

    logging.basicConfig(
        level=level or "INFO",
        format=fmt,
        datefmt="%H:%M:%S",
        handlers=[console_handler, file_handler],
    )
    setattr(root, _LOG_SENTINEL, True)
    root.info("Writing log output to %s", log_path)
