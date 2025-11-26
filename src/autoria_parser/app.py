"""High-level entry point for the Autoria parser."""
from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from .catalog import CatalogCrawler
from .config import AppConfig, load_config, read_input_urls
from .detail import ListingScraper
from .output import CSVWriter
from .playwright_client import PlaywrightSessionManager

logger = logging.getLogger(__name__)


def _clear_cache_directory(cache_dir: Path) -> None:
    """Remove all cached files before a new run."""
    path = Path(cache_dir).expanduser()
    if not path.exists():
        logger.info("Cache directory %s does not exist; nothing to clear.", path)
    else:
        logger.info("Clearing cache directory %s", path)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    path.mkdir(parents=True, exist_ok=True)


@dataclass
class AppState:
    config: AppConfig
    catalog_urls: List[str]
    site_label: str = "auto.ria.com"


def _detect_site(catalog_urls: List[str]) -> str:
    domains = {urlparse(url).netloc.lower() for url in catalog_urls if url}
    domains = {domain.split(":")[0] for domain in domains}  # strip port if present
    if not domains:
        return "auto.ria.com"
    if len(domains) > 1:
        raise ValueError(f"Mixed domains detected in input: {', '.join(sorted(domains))}. Provide URLs from a single site.")
    domain = domains.pop()
    if "agro.ria.com" in domain:
        return "agro.ria.com"
    return "auto.ria.com"


async def run(config_path: Path, input_path: Path, dry_run: bool = False, clear_cache: bool = False) -> None:
    """Main coroutine executed by the CLI."""
    config = load_config(config_path)
    catalog_urls = read_input_urls(input_path)
    site_label = _detect_site(catalog_urls)
    state = AppState(config=config, catalog_urls=catalog_urls, site_label=site_label)

    if clear_cache:
        _clear_cache_directory(state.config.cache.directory)

    logger.info("Loaded %s catalog URL(s) for %s", len(state.catalog_urls), state.site_label)
    logger.info("Configured %s data fields", len(state.config.dataFields))

    if dry_run:
        logger.info("Dry-run flag enabled; skipping Playwright bootstrap")
        return

    async with PlaywrightSessionManager(state.config, headless=state.config.playwright.headless) as manager:
        logger.info("Playwright launched (%s browser session(s))", manager.browser_count)
        crawler = CatalogCrawler(state.config, manager, site_label=state.site_label)
        listing_urls = await crawler.crawl(state.catalog_urls)
        logger.info("Total listing URLs collected: %s", len(listing_urls))

        if not listing_urls:
            logger.warning("No listings found; skipping detail scraping.")
            return

        scraper = ListingScraper(state.config, manager, site_label=state.site_label)
        writer: Optional[CSVWriter] = None
        output_path: Optional[Path] = None

        async def _write_batch(batch):
            nonlocal writer, output_path
            if writer is None:
                writer = CSVWriter(state.config)
                output_path = writer.path
            writer.write_batch(batch)

        try:
            summary = await scraper.scrape(listing_urls, on_batch=_write_batch)
        finally:
            if writer is not None:
                writer.close()

        logger.info("Scraped %s listing(s) after dedupe", summary.count)

        if summary.count == 0 or output_path is None:
            logger.warning("No listing data to write; CSV output skipped.")
            return

        logger.info("Results written to %s", output_path)
