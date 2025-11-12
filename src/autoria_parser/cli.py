"""Command-line parsing utilities."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, Optional


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scrape auto.ria.com search results")
    parser.add_argument(
        "--config",
        type=_path,
        default=Path("config.json").resolve(),
        help="Path to the JSON configuration file",
    )
    parser.add_argument(
        "--input",
        type=_path,
        default=Path("input.txt").resolve(),
        help="Path to the text file with catalog URLs (one per line)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"],
        help="Root logger level",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse config and inputs without hitting the network",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Clear the cache directory before scraping listings.",
    )
    return parser


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = build_parser()
    return parser.parse_args(list(argv) if argv is not None else None)
