"""Listing detail scraper with caching and phone deduplication."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Sequence, Set, Tuple

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


@dataclass
class ScrapeSummary:
    count: int
    results: List[ListingResult]


class ListingScraper:
    """Scrapes listing detail pages, reveals phone numbers, caches results, and deduplicates."""

    def __init__(self, config: AppConfig, manager: PlaywrightSessionManager, site_label: str = "auto.ria.com") -> None:
        self._config = config
        self._manager = manager
        self._site_label = site_label
        parsing = config.parsing
        self._page_timeout = parsing.pageLoadTimeout or 30_000
        self._delay_min = parsing.delayBetweenRequests.min
        self._delay_max = parsing.delayBetweenRequests.max
        self._phone_button_locators = self._resolve_phone_locators(config)
        self._ready_selectors = self._resolve_ready_selectors()
        self._cache_enabled = config.cache.enabled and config.cache.cacheListings
        self._cache_dir = Path(config.cache.directory).expanduser()
        if self._cache_enabled:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_ready_selectors(self) -> List[str]:
        if "agro.ria.com" in self._site_label:
            return [
                "xpath=//h1[contains(@class,'auto-head_title')]",
                "xpath=//div[contains(@class,'auto-head')]",
            ]
        return [LISTING_READY_SELECTOR]

    def _resolve_phone_locators(self, config: AppConfig) -> List[str]:
        if "agro.ria.com" in self._site_label:
            agro_custom = [f"xpath={xp}" for xp in getattr(config, "phoneButtonXpathsAgro", []) if xp.strip()]
            agro_default = [
                "xpath=//div[contains(@class,'sell-phone-btn')]//*[contains(text(),'Показать номер')]",
                "xpath=//section[@id='seller_info']//span[contains(@class,'button') and contains(text(),'Показать номер')]",
                "xpath=//button[contains(text(),'Показать номер')]",
                "xpath=//span[contains(@class,'button') and contains(text(),'Показать номер')]",
            ]
            return agro_custom if agro_custom else agro_default
        custom = [f"xpath={xp}" for xp in config.phoneButtonXpaths if xp.strip()]
        return custom

    async def scrape(
        self,
        listing_urls: Sequence[str],
        *,
        batch_size: int = 100,
        on_batch: Optional[Callable[[List[ListingResult]], Awaitable[None]]] = None,
    ) -> ScrapeSummary:
        urls = [url.strip() for url in listing_urls if url.strip()]
        if not urls:
            return ScrapeSummary(count=0, results=[])

        browsers = list(self._manager.browsers)
        if not browsers:
            raise RuntimeError("PlaywrightSessionManager is not running (no browsers available).")

        dedupe: Set[str] = set()
        results: List[ListingResult] = []
        batch: List[ListingResult] = []
        lock = asyncio.Lock()
        progress = {"count": 0}
        total_count = len(urls)

        queue: asyncio.Queue[str] = asyncio.Queue()
        for url in urls:
            queue.put_nowait(url)

        per_browser_workers = max(1, self._config.playwright.detailConcurrency)
        workers = []
        for handle in browsers:
            for _ in range(per_browser_workers):
                workers.append(
                    asyncio.create_task(
                        self._detail_worker(
                            handle,
                            queue,
                            results,
                            batch,
                            dedupe,
                            lock,
                            total_count,
                            batch_size,
                            on_batch,
                            progress,
                        )
                    )
                )

        await queue.join()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

        if on_batch and batch:
            await on_batch(list(batch))
            batch.clear()

        total_processed = progress["count"]
        logger.info("Listing scraping complete: %s rows (after dedupe)", total_processed)
        return ScrapeSummary(count=total_processed, results=[] if on_batch else results)

    async def _detail_worker(
        self,
        handle: BrowserHandle,
        queue: asyncio.Queue[str],
        results: List[ListingResult],
        batch: List[ListingResult],
        dedupe: Set[str],
        lock: asyncio.Lock,
        total_count: int,
        batch_size: int,
        on_batch: Optional[Callable[[List[ListingResult]], Awaitable[None]]],
        progress: Dict[str, int],
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
                chunk_to_flush: Optional[List[ListingResult]] = None
                processed = None
                while attempt <= self._config.errorRetryTimes:
                    try:
                        record = await self._process_listing(page, url)
                        if record is None:
                            logger.debug("Listing %s returned no data (skipped)", url)
                            break
                        normalized_phones = [_normalize_phone(p) for p in record.phones if _normalize_phone(p)]
                        async with lock:
                            if _should_skip_by_phone(dedupe, normalized_phones):
                                logger.info("Skipping listing %s due to duplicate phone", url)
                            else:
                                dedupe.update(normalized_phones)
                                if on_batch:
                                    batch.append(record)
                                    if len(batch) >= batch_size:
                                        chunk_to_flush = list(batch)
                                        batch.clear()
                                else:
                                    results.append(record)
                                progress["count"] += 1
                                processed = progress["count"]
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
                if chunk_to_flush and on_batch:
                    await on_batch(chunk_to_flush)
                if processed and (processed % 100 == 0 or processed == total_count):
                    logger.info("Scraped %s/%s listing(s)", processed, total_count)
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
            await page.goto(url, timeout=self._page_timeout, wait_until="domcontentloaded")
        except PlaywrightTimeoutError:
            raise
        except PlaywrightError as exc:
            if _is_denied_error(exc):
                raise ProxyDeniedError(str(exc))
            raise
        if "agro.ria.com" in self._site_label:
            try:
                await page.evaluate("document.body.style.zoom='0.25'")
            except Exception:
                pass
        try:
            html_preview = await page.content()
            logger.debug("Detail page HTML loaded for %s (length=%s)", page.url, len(html_preview))
        except Exception:
            logger.debug("Failed to fetch page content for %s", page.url)
        await self._click_phone_button(page)
        await self._wait_for_listing_ready(page)
        data = await self._extract_data_fields(page)

        if not data.get("title"):
            logger.info("Skipping listing without title: %s", page.url)
            return None

        phone_raw = data.get("phone")
        popup_phone_needed = not phone_raw or ("X" in phone_raw)
        if popup_phone_needed:
            popup_phone = await self._extract_phone_from_popup(page)
            if popup_phone:
                data["phone"] = popup_phone
                phone_raw = popup_phone
            elif (tel_fallback := await self._extract_phone_from_modal(page)):
                data["phone"] = tel_fallback
                phone_raw = tel_fallback

        phones = _split_phones(phone_raw)

        if not phones:
            logger.info("Skipping listing without phone: %s", page.url)
            return None

        result = ListingResult(url=page.url, data=data, phones=phones)
        if self._cache_enabled:
            await self._save_to_cache(url, result)
        return result

    async def _wait_for_listing_ready(self, page: Page) -> None:
        retries = 0
        while retries <= 1:
            for selector in self._ready_selectors:
                try:
                    await page.wait_for_selector(selector, timeout=self._page_timeout)
                    return
                except PlaywrightTimeoutError:
                    continue
            if retries == 0:
                retries += 1
                logger.info(
                    "Listing ready selectors %s not found on %s; reloading once",
                    self._ready_selectors,
                    page.url,
                )
                try:
                    await page.goto(page.url, timeout=self._page_timeout, wait_until="domcontentloaded")
                    continue
                except PlaywrightTimeoutError:
                    logger.warning("Reload timed out while waiting for listing ready on %s", page.url)
                    break
                except PlaywrightError as exc:
                    logger.warning("Reload failed while waiting for listing ready on %s: %s", page.url, exc)
                    break
            break
        logger.warning("Listing ready selectors %s not found on %s", self._ready_selectors, page.url)

    async def _click_phone_button(self, page: Page) -> None:
        if await self._any_phone_visible(page, timeout=1_000):
            logger.info("Phone already visible without click on %s", page.url)
            return

        await self._wait_for_phone_button(page)

        for selector in self._phone_button_locators:
            locator = page.locator(selector)
            try:
                count = await locator.count()
            except PlaywrightTimeoutError:
                continue
            if count == 0:
                logger.debug("No phone buttons found for selector %s on %s", selector, page.url)
                continue
            try:
                logger.debug("Found %s phone button candidate(s) with selector %s on %s", count, selector, page.url)
                for idx in range(min(count, 3)):  # try a few matches
                    target = locator.nth(idx)
                    try:
                        await target.scroll_into_view_if_needed(timeout=1_000)
                    except Exception:
                        pass
                    try:
                        logger.debug("Attempting phone button click selector=%s index=%s on %s", selector, idx, page.url)
                        await target.click(timeout=3_000, force=True)
                        logger.debug("Clicked phone button candidate %s (%s)", idx, selector)
                    except PlaywrightTimeoutError:
                        continue
                    except Exception:
                        continue
                    if await self._any_phone_visible(page, timeout=3_000):
                        logger.debug("Phone became visible after click on %s", page.url)
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
        selectors = field.xpathListAgro if "agro.ria.com" in self._site_label and field.xpathListAgro else field.xpathList
        for xp in selectors:
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

    async def _extract_phone_from_modal(self, page: Page) -> Optional[str]:
        selectors = [
            "xpath=//div[contains(@class,'react_modal__body')]//a[starts-with(@href,'tel:')]",
            "xpath=//div[@id='seller_info']//div[starts-with(@data-key,'phone')]//a[starts-with(@href,'tel:')]",
        ]
        for selector in selectors:
            modal_tel = page.locator(selector)
            try:
                await modal_tel.first.wait_for(state="visible", timeout=5_000)
                text = await modal_tel.first.text_content()
                cleaned = _clean_text(text)
                if cleaned:
                    return cleaned
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue
        return None

    async def _any_phone_visible(self, page: Page, timeout: int) -> bool:
        # Try legacy popup
        popup = page.locator("div.popup-inner")
        try:
            await popup.wait_for(state="visible", timeout=timeout)
            return True
        except PlaywrightTimeoutError:
            pass

        modal_tel = page.locator("xpath=//div[contains(@class,'react_modal__body')]//a[starts-with(@href,'tel:')]")
        inline_tel = page.locator(
            "xpath=//div[@id='seller_info']//div[starts-with(@data-key,'phone')]//a[starts-with(@href,'tel:')]"
        )
        try:
            await modal_tel.first.wait_for(state="visible", timeout=timeout)
            return True
        except PlaywrightTimeoutError:
            pass

        try:
            await inline_tel.first.wait_for(state="visible", timeout=timeout)
            return True
        except PlaywrightTimeoutError:
            return False

    async def _wait_for_phone_button(self, page: Page) -> None:
        for selector in self._phone_button_locators:
            try:
                await page.wait_for_selector(selector, timeout=2_000)
                logger.debug("Phone button visible with selector %s on %s", selector, page.url)
                return
            except PlaywrightTimeoutError:
                continue
        logger.info("Phone button not immediately visible on %s", page.url)

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
