"""Configuration models and helpers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field, validator


class DelaySettings(BaseModel):
    min: float = Field(..., ge=0, description="Minimum delay between catalog requests in seconds.")
    max: float = Field(..., ge=0, description="Maximum delay between catalog requests in seconds.")

    @validator("max")
    @classmethod
    def validate_range(cls, value: float, values) -> float:  # noqa: D417
        minimum = values.get("min")
        if minimum is not None and value < minimum:
            raise ValueError("max delay must be greater than or equal to min delay")
        return value


class ParsingSettings(BaseModel):
    threads: int = Field(1, ge=1)
    delayBetweenRequests: DelaySettings
    pageLoadTimeout: int = Field(30000, gt=0, description="Page load timeout in ms")
    waitForPaginationTimeout: int = Field(5000, gt=0, description="Pagination wait timeout in ms")
    listingsPerPage: Optional[int] = Field(
        None,
        ge=10,
        le=100,
        description="Desired number of listings per catalog page (10, 20, 30, 50, or 100).",
    )


class ProxySettings(BaseModel):
    enabled: bool = False
    rotation: bool = False
    list: List[str] = Field(default_factory=list)


class CacheSettings(BaseModel):
    enabled: bool = True
    directory: Path = Field(default=Path("./cache"))
    cacheListings: bool = True
    cacheCatalog: bool = False


class OutputSettings(BaseModel):
    file: Path = Field(default=Path("output.csv"))
    encoding: str = Field(default="utf-8")
    delimiter: str = Field(default=";")


class DataField(BaseModel):
    name: str
    xpathList: List[str] = Field(default_factory=list)


class PlaywrightSettings(BaseModel):
    headless: bool = True
    detailConcurrency: int = Field(5, ge=1, description="Max concurrent detail-page workers per browser.")
    maxBrowsers: int = Field(5, ge=1, description="Maximum number of simultaneous browser instances.")


class AppConfig(BaseModel):
    catalogXpaths: List[str] = Field(default_factory=list)
    paginationXpaths: List[str] = Field(default_factory=list)
    phoneButtonXpaths: List[str] = Field(default_factory=list)
    dataFields: List[DataField] = Field(default_factory=list)
    parsing: ParsingSettings = Field(default_factory=ParsingSettings)
    errorRetryTimes: int = Field(3, ge=0)
    proxy: ProxySettings = Field(default_factory=ProxySettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)
    playwright: PlaywrightSettings = Field(default_factory=PlaywrightSettings)

    def get_field(self, field_name: str) -> DataField | None:
        return next((field for field in self.dataFields if field.name == field_name), None)


def load_config(path: Path) -> AppConfig:
    """Load configuration from a JSON file."""
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return AppConfig.model_validate(data)


def read_input_urls(path: Path) -> List[str]:
    """Read search URLs from `input.txt`. Empty lines and comments (#) are ignored."""
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    urls = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    if not urls:
        raise ValueError("Input list is empty. Provide at least one catalog URL.")
    return urls
