"""Output helpers for persisting scraped data."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .config import AppConfig
from .detail import ListingResult


def write_csv(results: Sequence[ListingResult], config: AppConfig) -> Path:
    """Write listing results to CSV using the order defined in config.dataFields."""
    output_conf = config.output
    base_path = Path(output_conf.file).expanduser()
    timestamp = datetime.now().strftime("%Y%m%d-%H")
    if base_path.suffix.lower() == ".csv":
        file_path = base_path.with_name(f"{base_path.stem}_{timestamp}{base_path.suffix}")
    else:
        file_path = base_path / f"output_{timestamp}.csv"

    file_path.parent.mkdir(parents=True, exist_ok=True)

    field_names = [field.name for field in config.dataFields]
    if "url" not in field_names:
        field_names.append("url")

    with file_path.open("w", newline="", encoding=output_conf.encoding) as handle:
        writer = csv.DictWriter(handle, fieldnames=field_names, delimiter=output_conf.delimiter)
        writer.writeheader()
        for result in results:
            row = {}
            for name in field_names:
                if name == "url":
                    row[name] = result.url
                else:
                    row[name] = result.data.get(name, "") if result.data else ""
            writer.writerow(row)

    return file_path
