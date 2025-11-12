"""Listing detail scraper with caching and phone deduplication."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from playwright.async_api import BrowserContext, Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright._impl._errors import Error as PlaywrightError

from .config import AppConfig, DataField
from .exceptions import ProxyDeniedError
from .playwright_client import BrowserHandle, PlaywrightSessionManager

logger = logging.getLogger(__name__)

LISTING_READY_SELECTOR = "#basicInfo"
DENIED_ERROR_PATTERNS = [
    "ERR_PROXY_CONNECTION_FAILED",
    "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_INVALID_AUTH_CREDENTIALS",
    "ERR_CONNECTION_CLOSED",
    "403",
    "407",
]


@dataclass
class ListingResult:
    url: str
    data: Dict[str, Optional[str]]
    phones: List[str]


class ListingScraper:
    """Scrapes listing detail pages, reveals phone numbers, caches results, and deduplicates."""

    def __init__(self, config: AppConfig, manager: PlaywrightSessionManager) -> None:
        self._config = config
        self._manager = manager
        parsing = config.parsing
        self._page_timeout = parsing.pageLoadTimeout or 30_000
        self._delay_min = parsing.delayBetweenRequests.min
        self._delay_max = parsing.delayBetweenRequests.max
        self._phone_button_locators = [f"xpath={xp}" for xp in config.phoneButtonXpaths if xp.strip()]
        self._cache_enabled = config.cache.enabled and config.cache.cacheListings
        self._cache_dir = Path(config.cache.directory).expanduser()
        if self._cache_enabled:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    async def scrape(self, listing_urls: Sequence[str]) -> List[ListingResult]:
        urls = [url.strip() for url in listing_urls if url.strip()]
        if not urls:
            return []

        browsers = list(self._manager.browsers)
        if not browsers:
            raise RuntimeError("PlaywrightSessionManager is not running (no browsers available).")

        dedupe: Set[str] = set()
        results: List[ListingResult] = []
        lock = asyncio.Lock()

        queue: asyncio.Queue[str] = asyncio.Queue()
        for url in urls:
            queue.put_nowait(url)

        per_browser_workers = max(1, self._config.playwright.detailConcurrency)
        workers = []
        for handle in browsers:
            for _ in range(per_browser_workers):
                workers.append(
                    asyncio.create_task(self._detail_worker(handle, queue, results, dedupe, lock))
                )

        await queue.join()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

        logger.info("Listing scraping complete: %s rows (after dedupe)", len(results))
        return results

    async def _detail_worker(
        self,
        handle: BrowserHandle,
        queue: asyncio.Queue[str],
        results: List[ListingResult],
        dedupe: Set[str],
        lock: asyncio.Lock,
    ) -> None:
        async def open_page() -> Tuple[BrowserContext, Page]:
            context = await handle.browser.new_context()
            page = await context.new_page()
            return context, page

        context, page = await open_page()
        try:
            while True:
                try:
                    url = await queue.get()
                except asyncio.CancelledError:
                    break

                attempt = 0
                while attempt <= self._config.errorRetryTimes:
                    try:
                        record = await self._process_listing(page, url)
                        if record is not None:
                            normalized_phones = [_normalize_phone(p) for p in record.phones if _normalize_phone(p)]
                            async with lock:
                                if _should_skip_by_phone(dedupe, normalized_phones):
                                    logger.info("Skipping listing %s due to duplicate phone", url)
                                else:
                                    dedupe.update(normalized_phones)
                                    results.append(record)
                        break
                    except ProxyDeniedError as exc:
                        attempt += 1
                        logger.warning(
                            "Proxy denied while scraping %s (browser=%s): %s; rotating proxy.",
                            url,
                            handle.name,
                            exc,
                        )
                        await page.close()
                        await context.close()
                        await self._manager.rotate_browser(handle)
                        context, page = await open_page()
                    except Exception as exc:
                        attempt += 1
                        logger.error("Failed to scrape listing %s (browser=%s): %s", url, handle.name, exc, exc_info=True)
                        if attempt > self._config.errorRetryTimes:
                            logger.error("Giving up on listing %s after %s attempts", url, attempt)
                            break
                queue.task_done()
        finally:
            await page.close()
            await context.close()

    async def _process_listing(self, page: Page, url: str) -> Optional[ListingResult]:
        cache_hit = await self._load_from_cache(url)
        if cache_hit:
            logger.debug("Loaded listing from cache: %s", url)
            return cache_hit

        try:
            await page.goto(url, timeout=self._page_timeout)
        except PlaywrightTimeoutError:
            raise
        except PlaywrightError as exc:
            if _is_denied_error(exc):
                raise ProxyDeniedError(str(exc))
            raise
        await self._wait_for_listing_ready(page)
        await self._click_phone_button(page)
        data = await self._extract_data_fields(page)

        phone_raw = data.get("phone")
        popup_phone_needed = not phone_raw or ("X" in phone_raw)
        if popup_phone_needed:
            popup_phone = await self._extract_phone_from_popup(page)
            if popup_phone:
                data["phone"] = popup_phone
                phone_raw = popup_phone

        phones = _split_phones(phone_raw)

        result = ListingResult(url=page.url, data=data, phones=phones)
        if self._cache_enabled:
            await self._save_to_cache(url, result)
        return result

    async def _wait_for_listing_ready(self, page: Page) -> None:
        try:
            await page.wait_for_selector(LISTING_READY_SELECTOR, timeout=self._page_timeout)
        except PlaywrightTimeoutError:
            logger.warning("Listing ready selector %s not found on %s", LISTING_READY_SELECTOR, page.url)

    async def _click_phone_button(self, page: Page) -> None:
        if await self._ensure_phone_popup_visible(page):
            return

        for selector in self._phone_button_locators:
            locator = page.locator(selector)
            try:
                count = await locator.count()
            except PlaywrightTimeoutError:
                continue
            if count == 0:
                continue
            try:
                await locator.first.click(timeout=2_000)
                if await self._ensure_phone_popup_visible(page):
                    return
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue
        logger.debug("No phone button clicked on %s", page.url)

    async def _extract_data_fields(self, page: Page) -> Dict[str, Optional[str]]:
        data: Dict[str, Optional[str]] = {}
        for field in self._config.dataFields:
            value = await self._extract_single_field(page, field)
            data[field.name] = value
        data.setdefault("url", page.url)
        return data

    async def _extract_single_field(self, page: Page, field: DataField) -> Optional[str]:
        for xp in field.xpathList:
            selector = f"xpath={xp}"
            locator = page.locator(selector)
            try:
                if await locator.count() == 0:
                    continue
                text = await locator.first.text_content()
                if text:
                    cleaned = _clean_text(text)
                    if cleaned:
                        return cleaned
            except Exception:
                continue
        return None

    async def _extract_phone_from_popup(self, page: Page) -> Optional[str]:
        popup = await self._ensure_phone_popup_visible(page)
        if not popup:
            return None
        button_selector = "button[data-action='call'] span, a[href^='tel:'] span"
        button = popup.locator(button_selector)
        try:
            if await button.count() == 0:
                return None
            text = await button.first.text_content()
            return _clean_text(text)
        except PlaywrightTimeoutError:
            return None
        except Exception:
            return None

    async def _ensure_phone_popup_visible(self, page: Page) -> Optional["Locator"]:
        popup = page.locator("div.popup-inner")
        try:
            await popup.wait_for(state="visible", timeout=3_000)
            return popup
        except PlaywrightTimeoutError:
            return None

    async def _load_from_cache(self, url: str) -> Optional[ListingResult]:
        if not self._cache_enabled:
            return None
        path = self._cache_path(url)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            data = payload.get("data", {})
            cached_phone = (data or {}).get("phone")
            cached_phones = payload.get("phones", [])
            if (cached_phone and "X" in cached_phone) or not cached_phones:
                logger.debug("Cache entry for %s contains masked/empty phone; re-scraping.", url)
                return None
            return ListingResult(
                url=payload.get("url", url),
                data=data,
                phones=cached_phones,
            )
        except Exception as exc:
            logger.warning("Failed to load cache for %s: %s", url, exc)
            return None

    async def _save_to_cache(self, url: str, result: ListingResult) -> None:
        path = self._cache_path(url)
        payload = {"url": result.url, "data": result.data, "phones": result.phones}
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to write cache for %s: %s", url, exc)

    def _cache_path(self, url: str) -> Path:
        fingerprint = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self._cache_dir / f"{fingerprint}.json"

def _clean_text(text: str) -> str:
    return " ".join(text.split()) if text else ""


def _split_phones(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    # Split on commas, whitespace, plus "·" bullet etc.
    bits = re.split(r"[,\n·;]+", raw)
    phones = []
    for bit in bits:
        cleaned = bit.strip()
        if cleaned:
            phones.append(cleaned)
    return phones


def _normalize_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    digits = re.sub(r"\D+", "", phone)
    return digits or None


def _should_skip_by_phone(seen: Set[str], phones: Sequence[Optional[str]]) -> bool:
    for phone in phones:
        if not phone:
            continue
        if phone in seen:
            return True
    return False


def _is_denied_error(exc: Exception) -> bool:
    message = str(exc)
    return any(pattern in message for pattern in DENIED_ERROR_PATTERNS)
