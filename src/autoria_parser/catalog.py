"""Catalog pagination crawler implemented with Playwright."""
from __future__ import annotations

import asyncio
import logging
import random
from typing import List, Optional, Sequence, Set, Tuple
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from playwright.async_api import BrowserContext, ElementHandle, Page, TimeoutError as PlaywrightTimeoutError
from playwright._impl._errors import Error as PlaywrightError

from .config import AppConfig
from .exceptions import ProxyDeniedError
from .playwright_client import BrowserHandle, PlaywrightSessionManager

logger = logging.getLogger(__name__)

ITEMS_CONTAINER_SELECTOR = "#items .items-list"
PAGINATION_FALLBACK_SELECTOR = "nav.pagination"
DENIED_ERROR_PATTERNS = [
    "ERR_PROXY_CONNECTION_FAILED",
    "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_INVALID_AUTH_CREDENTIALS",
    "ERR_CONNECTION_CLOSED",
    "403",
    "407",
]


class CatalogCrawler:
    """Downloads catalog pages, walks pagination, and extracts listing URLs."""

    def __init__(self, config: AppConfig, manager: PlaywrightSessionManager, site_label: str = "auto.ria.com") -> None:
        self._config = config
        self._manager = manager
        self._site_label = site_label
        parsing = config.parsing
        self._page_timeout = parsing.pageLoadTimeout or 30_000
        self._pagination_wait_timeout = parsing.waitForPaginationTimeout or 5_000
        self._ready_wait_timeout = self._pagination_wait_timeout if "agro.ria.com" in self._site_label else self._page_timeout
        self._pagination_wait_timeout_effective = 2_000 if "agro.ria.com" in self._site_label else self._pagination_wait_timeout
        self._delay_min = parsing.delayBetweenRequests.min
        self._delay_max = parsing.delayBetweenRequests.max
        self._catalog_locators = self._resolve_catalog_locators(config)
        self._pagination_locators = self._resolve_pagination_locators(config)
        self._catalog_ready_selectors = self._resolve_catalog_ready_selectors()
        self._pagination_fallback_selector = self._resolve_pagination_fallback()
        self._desired_page_size = parsing.listingsPerPage

    async def crawl(self, catalog_urls: Sequence[str]) -> List[str]:
        """Return a de-duplicated list of listing URLs."""
        urls = [url.strip() for url in catalog_urls if url.strip()]
        if not urls:
            return []

        browser_handles = list(self._manager.browsers)
        if not browser_handles:
            raise RuntimeError("PlaywrightSessionManager is not running (no browsers available).")

        assignments = self._assign_urls(batch_size=len(browser_handles), urls=urls)

        tasks = []
        for handle, assigned in zip(browser_handles, assignments):
            if not assigned:
                continue
            tasks.append(asyncio.create_task(self._crawl_with_browser(handle, assigned)))

        if not tasks:
            return []

        listing_sets = await asyncio.gather(*tasks)
        merged: Set[str] = set().union(*listing_sets)
        logger.info("Catalog crawl finished with %s unique listing URL(s)", len(merged))
        return sorted(merged)

    async def _crawl_with_browser(self, handle: BrowserHandle, urls: Sequence[str]) -> Set[str]:
        logger.debug("Browser %s (proxy=%s) processing %s catalog URL(s)", handle.name, handle.proxy_label or "direct", len(urls))
        collected: Set[str] = set()

        async def open_page() -> Tuple[BrowserContext, Page]:
            context = await handle.browser.new_context()
            page = await context.new_page()
            return context, page

        context, page = await open_page()
        try:
            for url in urls:
                logger.info("Browser %s loading catalog: %s", handle.name, url)
                attempt = 0
                while attempt <= self._config.errorRetryTimes:
                    try:
                        links = await self._crawl_single_catalog(page, url)
                        collected.update(links)
                        break
                    except ProxyDeniedError as exc:
                        attempt += 1
                        logger.warning(
                            "Proxy denied while loading catalog %s (browser=%s): %s; rotating proxy.",
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
                        logger.error(
                            "Failed to crawl catalog %s (browser=%s): %s",
                            url,
                            handle.name,
                            exc,
                            exc_info=True,
                        )
                        if attempt > self._config.errorRetryTimes:
                            logger.error("Giving up on catalog %s after %s attempts", url, attempt)
                            break
        finally:
            await page.close()
            await context.close()
        return collected

    async def _crawl_single_catalog(self, page: Page, url: str) -> Set[str]:
        url = self._apply_page_size(url)
        wait_until = "load" if "agro.ria.com" in self._site_label else "domcontentloaded"
        try:
            await page.goto(url, timeout=self._page_timeout, wait_until=wait_until)
        except PlaywrightTimeoutError:
            raise
        except PlaywrightError as exc:
            if _is_denied_error(exc):
                raise ProxyDeniedError(str(exc))
            raise
        await self._wait_for_catalog_ready(page)

        catalog_links: Set[str] = set()
        pages_seen: Set[str] = set()
        while True:
            canonical_url = page.url.split("#", 1)[0]
            if canonical_url in pages_seen:
                logger.debug("Detected repeated catalog page (%s); stopping pagination loop", canonical_url)
                break
            pages_seen.add(canonical_url)
            before = len(catalog_links)
            catalog_links.update(await self._extract_catalog_links(page))
            added = len(catalog_links) - before
            logger.info("Catalog page %s extracted %s new link(s) (total %s)", canonical_url, added, len(catalog_links))
            has_next = await self._go_to_next_page(page)
            if not has_next:
                break
        return catalog_links

    async def _extract_catalog_links(self, page: Page) -> Set[str]:
        links: Set[str] = set()
        selectors = self._catalog_locators or [
            "xpath=//a[@data-car-id]",
            "xpath=//section[contains(@class,'proposition')]//a[contains(@class,'proposition_link')]",
        ]
        for selector in selectors:
            locator = page.locator(selector)
            try:
                elements = await locator.element_handles()
            except PlaywrightTimeoutError:
                continue
            for element in elements:
                href = await element.get_attribute("href")
                if not href:
                    continue
                absolute = urljoin(page.url, href.strip())
                links.add(absolute)
        logger.debug("Extracted %s links from %s", len(links), page.url)
        return links

    async def _go_to_next_page(self, page: Page) -> bool:
        if self._is_last_page(page.url):
            logger.info("Detected last page (%s); stopping pagination.", page.url)
            return False

        next_url = self._compute_next_page_url(page.url)
        logger.info("Advancing pagination via URL increment: %s -> %s", page.url, next_url)
        return await self._navigate_to_url(page, next_url)

    async def _wait_for_catalog_ready(self, page: Page) -> None:
        for selector in self._catalog_ready_selectors:
            try:
                await page.wait_for_selector(selector, timeout=self._ready_wait_timeout, state="visible")
                break
            except PlaywrightTimeoutError:
                continue

        await self._wait_for_any_selector(
            page,
            self._pagination_locators,
            fallback_selector=self._pagination_fallback_selector or PAGINATION_FALLBACK_SELECTOR,
        )

    async def _wait_for_any_selector(self, page: Page, selectors: Sequence[str], fallback_selector: Optional[str] = None) -> None:
        for selector in selectors:
            try:
                await page.wait_for_selector(selector, timeout=self._pagination_wait_timeout_effective, state="visible")
                return
            except PlaywrightTimeoutError:
                continue

        if fallback_selector:
            try:
                await page.wait_for_selector(fallback_selector, timeout=self._pagination_wait_timeout_effective, state="visible")
                return
            except PlaywrightTimeoutError:
                pass
        logger.debug("Pagination selectors not visible on %s; continuing anyway", page.url)

    async def _delay_between_requests(self) -> None:
        if self._delay_max <= 0:
            return
        if self._delay_max == self._delay_min:
            delay = self._delay_min
        else:
            delay = random.uniform(self._delay_min, self._delay_max)
        if delay > 0:
            await asyncio.sleep(delay)

    @staticmethod
    def _assign_urls(batch_size: int, urls: Sequence[str]) -> List[List[str]]:
        batches = [[] for _ in range(batch_size)]
        for idx, url in enumerate(urls):
            batches[idx % batch_size].append(url)
        return batches

    @staticmethod
    async def _is_disabled(handle: ElementHandle) -> bool:
        attr_disabled = await handle.get_attribute("disabled")
        if attr_disabled is not None:
            return True
        aria_disabled = await handle.get_attribute("aria-disabled")
        if aria_disabled and aria_disabled.lower() == "true":
            return True
        class_attr = await handle.get_attribute("class")
        if class_attr and "disabled" in class_attr.lower():
            return True
        return False

    async def _page_has_items(self, page: Page) -> bool:
        selectors = self._catalog_locators or ["xpath=//a[@data-car-id]"]
        for selector in selectors:
            locator = page.locator(selector)
            try:
                if await locator.count() > 0:
                    return True
            except PlaywrightTimeoutError:
                continue
        # Agro fallback: propositions under search-results
        if "agro.ria.com" in self._site_label:
            agro_locator = page.locator("xpath=//div[@class='search-results']//div[contains(@class,'proposition')]")
            try:
                if await agro_locator.count() > 0:
                    return True
            except PlaywrightTimeoutError:
                pass
        return False

    def _compute_next_page_url(self, current_url: str) -> Optional[str]:
        parsed = urlparse(current_url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if "page" in query and query["page"]:
            try:
                current_page = int(query["page"][-1])
            except ValueError:
                current_page = -1
        else:
            # Agro uses page=2 as the first paginated page; auto starts at 1.
            if "agro.ria.com" in self._site_label:
                current_page = 1
            else:
                current_page = 0

        next_page = current_page + 1
        return self._build_url_with_page(current_url, next_page)

    async def _nav_next_href(self, page: Page) -> Optional[str]:
        nav = page.locator("nav.pagination")
        if await nav.count() == 0:
            return None
        # Prefer the explicit "Next" button if present inside the nav.
        next_button = nav.locator("button[aria-label='Next']")
        try:
            if await next_button.count():
                href = await next_button.first.get_attribute("href")
                if href:
                    return self._resolve_page_href(page.url, href)
        except PlaywrightTimeoutError:
            pass

        items = nav.locator("li")
        count = await items.count()
        active_found = False
        for idx in range(count):
            item = items.nth(idx)
            class_attr = await item.get_attribute("class") or ""
            if "active" in class_attr:
                active_found = True
                continue
            if not active_found:
                continue
            link = item.locator("a")
            try:
                if await link.count() == 0:
                    continue
                href = await link.first.get_attribute("href")
                if href:
                    return self._resolve_page_href(page.url, href)
            except PlaywrightTimeoutError:
                continue
            break
        return None

    async def _navigate_to_url(self, page: Page, target: str) -> bool:
        await self._delay_between_requests()
        previous_url = page.url
        target = self._apply_page_size(target)
        wait_until = "load" if "agro.ria.com" in self._site_label else "domcontentloaded"
        try:
            await page.goto(target, timeout=self._page_timeout, wait_until=wait_until)
        except PlaywrightTimeoutError:
            logger.warning("Timed out while navigating to %s; continuing with partial load.", target)
        except PlaywrightError as exc:
            if _is_denied_error(exc):
                raise ProxyDeniedError(str(exc))
            raise
        return await self._post_navigation(page, previous_url)

    async def _navigate_via_click(self, page: Page, handle: ElementHandle) -> bool:
        await self._delay_between_requests()
        previous_url = page.url
        try:
            await handle.click()
        except PlaywrightTimeoutError:
            logger.warning("Timed out clicking pagination control")
            return False
        return await self._post_navigation(page, previous_url)

    async def _post_navigation(self, page: Page, previous_url: str) -> bool:
        try:
            state = "load" if "agro.ria.com" in self._site_label else "domcontentloaded"
            await page.wait_for_load_state(state, timeout=self._page_timeout)
        except PlaywrightTimeoutError:
            logger.warning("Timed out waiting for %s after navigation; falling back to selector checks.", state)

        await self._wait_for_catalog_ready(page)

        if page.url == previous_url:
            logger.debug("Navigation did not change page (%s); skipping", page.url)
            return False

        if not await self._page_has_items(page):
            logger.debug("No listings detected on %s after navigation; assuming end of pagination", page.url)
            return False

        await asyncio.sleep(0.5)
        return True

    def _resolve_page_href(self, current_url: str, href: str) -> str:
        href = (href or "").strip()
        if not href:
            return current_url
        parsed_base = urlparse(current_url)
        parsed_href = urlparse(href)
        href_query = parse_qs(parsed_href.query, keep_blank_values=True)
        if "page" in href_query and href_query["page"]:
            page_value = href_query["page"][-1]
            return self._build_url_with_page(current_url, int(page_value))

        if parsed_href.path == parsed_base.path:
            # Same path but no page query -> increment from current URL.
            parsed_current = urlparse(current_url)
            current_query = parse_qs(parsed_current.query, keep_blank_values=True)
            current_page = current_query.get("page", ["0"])[-1]
            try:
                next_page = int(current_page) + 1
            except ValueError:
                next_page = 1
            return self._build_url_with_page(current_url, next_page)

        resolved = urljoin(current_url, href)
        logger.debug("Resolved pagination href %s -> %s", href, resolved)
        return self._apply_page_size(resolved)

    def _build_url_with_page(self, current_url: str, page_value: int) -> str:
        parsed = urlparse(current_url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["page"] = [str(page_value)]
        if self._desired_page_size:
            query["limit"] = [str(self._desired_page_size)]
        merged = urlencode(query, doseq=True)
        new_url = urlunparse(parsed._replace(query=merged))
        logger.debug("Built pagination URL: %s -> %s", current_url, new_url)
        return new_url

    def _is_last_page(self, current_url: str) -> bool:
        parsed = urlparse(current_url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        total_pages = query.get("pages_count", []) or query.get("pagesCount", [])
        current_page = query.get("page", ["0"])[-1]
        try:
            current_num = int(current_page)
        except ValueError:
            current_num = 0

        if total_pages:
            try:
                max_page = int(total_pages[-1]) - 1
                return current_num >= max_page
            except ValueError:
                return False
        return False

    def _apply_page_size(self, url: str) -> str:
        if "agro.ria.com" in self._site_label:
            return url
        if not self._desired_page_size:
            return url
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["limit"] = [str(self._desired_page_size)]
        new_url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
        return new_url

    def _resolve_catalog_locators(self, config: AppConfig) -> List[str]:
        if "agro.ria.com" in self._site_label:
            agro_custom = [f"xpath={xp}" for xp in getattr(config, "catalogXpathsAgro", []) if xp.strip()]
            agro_fallback = [
                "xpath=//div[contains(@class,'search-results')]//div[contains(@class,'proposition')]//a[contains(@class,'proposition_link')]",
                "xpath=//div[contains(@class,'na-gallery-view')]//div[contains(@class,'proposition')]//a[contains(@class,'proposition_link')]",
            ]
            return agro_custom if agro_custom else agro_fallback

        custom = [f"xpath={xp}" for xp in config.catalogXpaths if xp.strip()]
        agro_fallback = [
            "xpath=//div[@class='search-results']//div[contains(@class,'proposition')]//a[contains(@class,'proposition_link')]",
            "xpath=//div[contains(@class,'na-gallery-view')]//div[contains(@class,'proposition')]//a[contains(@class,'proposition_link')]",
        ]
        auto_fallback = [
            "xpath=//a[@data-car-id]",
            "xpath=//section[contains(@class,'proposition')]//a[contains(@class,'proposition_link')]",
        ]
        if "agro.ria.com" in self._site_label:
            return custom + agro_fallback if custom else agro_fallback
        return custom if custom else auto_fallback

    def _resolve_pagination_locators(self, config: AppConfig) -> List[str]:
        if "agro.ria.com" in self._site_label:
            custom_agro = [f"xpath={xp}" for xp in getattr(config, "paginationXpathsAgro", []) if xp.strip()]
            agro_default = [
                "xpath=//div[contains(@class,'pager')]//a[contains(@href,'page=')]",
                "xpath=//span[contains(@class,'page-item')]/a[contains(@href,'page=')]",
            ]
            return custom_agro if custom_agro else agro_default
        custom = [f"xpath={xp}" for xp in config.paginationXpaths if xp.strip()]
        return custom

    def _resolve_catalog_ready_selectors(self) -> List[str]:
        if "agro.ria.com" in self._site_label:
            return []
        return [ITEMS_CONTAINER_SELECTOR]

    def _resolve_pagination_fallback(self) -> Optional[str]:
        if "agro.ria.com" in self._site_label:
            return "div.pager"
        return PAGINATION_FALLBACK_SELECTOR


def _is_denied_error(exc: Exception) -> bool:
    message = str(exc)
    return any(pattern in message for pattern in DENIED_ERROR_PATTERNS)
