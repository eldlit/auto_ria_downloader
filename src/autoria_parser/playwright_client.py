"""Playwright bootstrap and browser-session management."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from typing import Deque, Iterable, List, Optional, Tuple

from playwright.async_api import Browser, Playwright, async_playwright

from .config import AppConfig

logger = logging.getLogger(__name__)


def _format_proxy_entry(raw: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Convert shorthand proxy definitions into (server, username, password)."""
    value = raw.strip()
    if not value:
        raise ValueError("Proxy entries must be non-empty strings")

    parts = value.split(":")
    if len(parts) >= 4:
        host, port, username, password = parts[:4]
        return (f"http://{host}:{port}", username, password)
    if len(parts) == 3:
        host, port, username = parts
        password = ""
        return (f"http://{host}:{port}", username, password)
    if "://" in value:
        return (value, None, None)
    return (f"http://{value}", None, None)


@dataclass
class BrowserHandle:
    """Represents a launched Playwright browser tied to a single proxy."""

    name: str
    proxy_label: Optional[str]
    browser: Browser
    proxy_entry: Optional[Tuple[str, Optional[str], Optional[str]]]


class PlaywrightSessionManager:
    """Starts Playwright and launches one browser per proxy (or a default browser)."""

    def __init__(self, config: AppConfig, headless: bool = True) -> None:
        self._config = config
        self._headless = headless
        self._playwright: Optional[Playwright] = None
        self._browsers: List[BrowserHandle] = []
        self._startup_lock = asyncio.Lock()
        self._reserve_proxies: Deque[Optional[Tuple[str, Optional[str], Optional[str]]]] = deque()
        self._max_browsers = max(1, config.playwright.maxBrowsers)

    async def __aenter__(self) -> "PlaywrightSessionManager":
        await self._startup()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: D401
        await self.aclose()

    async def _startup(self) -> None:
        async with self._startup_lock:
            if self._playwright is not None:
                return

            logger.debug("Starting Playwright runtime")
            self._playwright = await async_playwright().start()

            proxy_entries = self._build_proxy_entries()
            if not proxy_entries:
                proxy_entries = [None]

            while len(proxy_entries) < self._max_browsers:
                proxy_entries.append(None)

            active_entries = proxy_entries[: self._max_browsers]
            self._reserve_proxies = deque(proxy_entries[self._max_browsers :])
            if not self._reserve_proxies:
                self._reserve_proxies.append(None)

            for idx, proxy in enumerate(active_entries):
                handle = await self._create_handle(proxy, idx)
                self._browsers.append(handle)

            logger.info("Playwright ready with %s browser session(s)", len(self._browsers))

    async def aclose(self) -> None:
        """Close all browsers and stop Playwright."""
        while self._browsers:
            handle = self._browsers.pop()
            logger.debug("Closing browser %s (proxy=%s)", handle.name, handle.proxy_label or "direct")
            try:
                await handle.browser.close()
            except Exception as exc:  # pragma: no cover - best-effort cleanup
                logger.warning("Failed to close browser %s: %s", handle.name, exc)

        if self._playwright is not None:
            logger.debug("Stopping Playwright runtime")
            await self._playwright.stop()
            self._playwright = None

    @property
    def browsers(self) -> Iterable[BrowserHandle]:
        return tuple(self._browsers)

    @property
    def browser_count(self) -> int:
        return len(self._browsers)

    def _build_proxy_entries(self) -> List[Optional[Tuple[str, Optional[str], Optional[str]]]]:
        proxy_settings = self._config.proxy
        if proxy_settings.enabled and proxy_settings.list:
            entries = [_format_proxy_entry(p) for p in proxy_settings.list if p.strip()]
            return entries or [None]
        return [None]

    async def rotate_browser(self, handle: BrowserHandle) -> None:
        """Rotate the browser to use the next available proxy."""
        if self._playwright is None:
            return
        old_proxy = handle.proxy_entry
        new_proxy = None
        if self._reserve_proxies:
            new_proxy = self._reserve_proxies.popleft()
        elif old_proxy is None:
            logger.warning("No spare proxies available; continuing with direct connection.")
            return
        else:
            logger.warning("No spare proxies available; reusing existing proxy.")
            return

        logger.info("Rotating browser %s to proxy=%s", handle.name, new_proxy[0] if new_proxy else "direct")
        new_browser = await self._launch_browser_instance(new_proxy)
        await handle.browser.close()
        handle.browser = new_browser
        handle.proxy_entry = new_proxy
        handle.proxy_label = new_proxy[0] if new_proxy else None

        if old_proxy is not None:
            self._reserve_proxies.append(old_proxy)

    async def _create_handle(
        self, proxy: Optional[Tuple[str, Optional[str], Optional[str]]], idx: int
    ) -> BrowserHandle:
        browser = await self._launch_browser_instance(proxy)
        label = proxy[0] if proxy else "direct"
        return BrowserHandle(
            name=f"browser-{idx}",
            proxy_label=label if proxy else None,
            browser=browser,
            proxy_entry=proxy,
        )

    async def _launch_browser_instance(
        self, proxy: Optional[Tuple[str, Optional[str], Optional[str]]]
    ) -> Browser:
        proxy_conf = None
        if proxy:
            server, username, password = proxy
            proxy_conf = {"server": server}
            if username:
                proxy_conf["username"] = username
            if password:
                proxy_conf["password"] = password
            logger.info("Launching Chromium with proxy=%s", server)
        else:
            logger.info("Launching Chromium with direct connection")

        return await self._playwright.chromium.launch(headless=self._headless, proxy=proxy_conf)
