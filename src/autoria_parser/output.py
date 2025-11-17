"""Output helpers for persisting scraped data."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .config import AppConfig
from .detail import ListingResult


def _normalized_encoding(encoding: str) -> str:
    return encoding.replace("-", "").replace("_", "").lower()


def _needs_utf8_bom(encoding: str) -> bool:
    normalized = _normalized_encoding(encoding)
    return normalized in {"utf8", "utf"}


class CSVWriter:
    """Incremental CSV writer that preserves the configured column order."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._field_names = [field.name for field in config.dataFields]
        if "url" not in self._field_names:
            self._field_names.append("url")
        self.path = _build_output_path(config)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoding = config.output.encoding or "utf-8"
        self._handle = self.path.open("w", newline="", encoding=encoding)
        if _needs_utf8_bom(encoding):
            self._handle.write("\ufeff")
        self._writer = csv.DictWriter(
            self._handle,
            fieldnames=self._field_names,
            delimiter=config.output.delimiter,
        )
        self._writer.writeheader()
        self._closed = False

    def write_batch(self, batch: Sequence[ListingResult]) -> None:
        for result in batch:
            row = {}
            for name in self._field_names:
                if name == "url":
                    row[name] = result.url
                else:
                    row[name] = result.data.get(name, "") if result.data else ""
            self._writer.writerow(row)

    def close(self) -> None:
        if not self._closed:
            self._handle.close()
            self._closed = True

    def __enter__(self) -> "CSVWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: D401
        self.close()


def write_csv(results: Sequence[ListingResult], config: AppConfig) -> Path:
    """Write listing results to CSV using the order defined in config.dataFields."""
    with CSVWriter(config) as writer:
        writer.write_batch(results)
        return writer.path


def _build_output_path(config: AppConfig) -> Path:
    output_conf = config.output
    base_path = Path(output_conf.file).expanduser()
    timestamp = datetime.now().strftime("%Y%m%d-%H")
    if base_path.suffix.lower() == ".csv":
        return base_path.with_name(f"{base_path.stem}_{timestamp}{base_path.suffix}")
    return base_path / f"output_{timestamp}.csv"
