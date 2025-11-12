"""Logging helpers for the Autoria parser."""
from __future__ import annotations

import logging
from typing import Optional

from rich.logging import RichHandler


def setup_logging(level: Optional[str] = None) -> None:
    """Configure Rich logging only once."""
    root = logging.getLogger()
    if any(isinstance(handler, RichHandler) for handler in root.handlers):
        return

    handler = RichHandler(rich_tracebacks=True, markup=False)
    fmt = "%(message)s"
    logging.basicConfig(level=level or "INFO", format=fmt, datefmt="%H:%M:%S", handlers=[handler])
