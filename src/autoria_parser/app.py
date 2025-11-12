"""High-level entry point for the Autoria parser."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .catalog import CatalogCrawler
from .config import AppConfig, load_config, read_input_urls
from .detail import ListingScraper
from .output import write_csv
from .playwright_client import PlaywrightSessionManager

logger = logging.getLogger(__name__)


@dataclass
class AppState:
    config: AppConfig
    catalog_urls: List[str]


async def run(config_path: Path, input_path: Path, dry_run: bool = False) -> None:
    """Main coroutine executed by the CLI."""
    config = load_config(config_path)
    catalog_urls = read_input_urls(input_path)
    state = AppState(config=config, catalog_urls=catalog_urls)

    logger.info("Loaded %s catalog URL(s)", len(state.catalog_urls))
    logger.info("Configured %s data fields", len(state.config.dataFields))

    if dry_run:
        logger.info("Dry-run flag enabled; skipping Playwright bootstrap")
        return

    # TODO: implement catalog pagination, listing scraping, caching, and output generation.
    async with PlaywrightSessionManager(state.config, headless=state.config.playwright.headless) as manager:
        logger.info("Playwright launched (%s browser session(s))", manager.browser_count)
        crawler = CatalogCrawler(state.config, manager)
        listing_urls = await crawler.crawl(state.catalog_urls)
        logger.info("Total listing URLs collected: %s", len(listing_urls))

        if not listing_urls:
            logger.warning("No listings found; skipping detail scraping.")
            return

        scraper = ListingScraper(state.config, manager)
        listing_results = await scraper.scrape(listing_urls)
        logger.info("Scraped %s listing(s) after dedupe", len(listing_results))

        if not listing_results:
            logger.warning("No listing data to write; CSV output skipped.")
            return

        output_path = write_csv(listing_results, state.config)
        logger.info("Results written to %s", output_path)
