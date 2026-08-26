"""Persistent Microsoft Edge context with manual-only authentication."""

from __future__ import annotations

import logging
import threading
import time
from urllib.parse import urlsplit

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from config import Settings
from errors import SafetyStop
from page_guard import BLOCKED_MARKERS, normalize_text, sanitize_url


class BrowserSession:
    def __init__(
        self,
        settings: Settings,
        logger: logging.Logger,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.settings = settings
        self.logger = logger
        self._playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self.stop_event = stop_event

    def __enter__(self) -> "BrowserSession":
        self.settings.user_data_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        try:
            self.context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.settings.user_data_dir),
                channel=self.settings.browser_channel,
                headless=False,
                no_viewport=True,
                args=["--start-maximized"],
                chromium_sandbox=True,
            )
        except Exception:
            self._playwright.stop()
            self._playwright = None
            raise
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.context is not None:
            try:
                self.context.close()
            except Exception:
                # Manual Edge close is an expected release path. Do not mask
                # the original result with a TargetClosedError during cleanup.
                self.logger.info("Dedicated browser was already closed during cleanup")
            finally:
                self.context = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                self.logger.info("Playwright driver was already closed during cleanup")
            finally:
                self._playwright = None

    def open_cart(self) -> Page:
        if self.context is None:
            raise RuntimeError("Browser context is not open")
        page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.logger.info("Opening verified Shopping Cart path: %s", self.settings.shopping_cart_url)
        page.goto(
            self.settings.shopping_cart_url,
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        return page

    def wait_for_manual_login_and_cart(self) -> Page:
        """Wait while the user manually completes login/MFA and opens the cart."""

        if self.context is None:
            raise RuntimeError("Browser context is not open")
        deadline = time.monotonic() + self.settings.manual_login_timeout_seconds
        last_log = 0.0
        while time.monotonic() < deadline:
            if self.stop_event is not None and self.stop_event.is_set():
                raise SafetyStop("Stopped by user while waiting for manual login")
            for page in self.context.pages:
                if page.is_closed():
                    continue
                self._stop_on_security_page(page)
                parts = urlsplit(page.url)
                if (
                    parts.hostname == "sisn.hkust-gz.edu.cn"
                    and parts.path.rstrip("/") == "/student/student-my-cart"
                    and page.locator(self.settings.enroll_button_selector).count() > 0
                ):
                    self.logger.info(
                        "Shopping Cart route/button detected after manual login; "
                        "cart data may still be loading: %s",
                        sanitize_url(page.url),
                    )
                    self.close_other_pages(page)
                    return page
            if time.monotonic() - last_log >= 15:
                self.logger.info(
                    "Waiting for user to complete login/MFA and open Shopping Cart"
                )
                last_log = time.monotonic()
            time.sleep(1)
        raise SafetyStop("Timed out waiting for manual login and Shopping Cart")

    def close_other_pages(self, cart_page: Page) -> None:
        """Keep only the verified cart page inside this dedicated context."""

        if self.context is None:
            return
        closed = 0
        for page in list(self.context.pages):
            if page is cart_page or page.is_closed():
                continue
            page.close()
            closed += 1
        if closed:
            self.logger.info(
                "Closed %d extra dedicated-browser page(s); kept Shopping Cart",
                closed,
            )

    def wait_for_user_release(self, cart_page: Page) -> None:
        """Keep the cart visible until Safe Stop or manual browser close."""

        self.logger.info(
            "Shopping Cart browser will remain open until Safe Stop or manual close"
        )
        while True:
            try:
                if cart_page.is_closed():
                    return
            except Exception:
                return
            if self.stop_event is not None and self.stop_event.is_set():
                return
            try:
                if self.context is None or not self.context.pages:
                    return
            except Exception:
                return
            time.sleep(0.25)

    def _stop_on_security_page(self, page: Page) -> None:
        try:
            body = normalize_text(page.locator("body").inner_text(timeout=500))
        except Exception:
            return
        found = [marker for marker in BLOCKED_MARKERS if marker in body]
        if found:
            raise SafetyStop(
                "Security/maintenance/queue page detected; switch to manual operation: "
                + ", ".join(found)
            )
