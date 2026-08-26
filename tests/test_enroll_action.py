from __future__ import annotations

import unittest

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from enroll_action import EnrollAction
from errors import DuplicateClickPrevented


class EnrollActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(
            channel="msedge", headless=True, chromium_sandbox=True
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.browser.close()
        cls.playwright.stop()

    def test_only_one_click_on_simulated_html(self) -> None:
        page = self.browser.new_page()
        page.set_content(
            """
            <button class="btn-cart-enrol" onclick="window.clicks += 1">Enrol</button>
            <script>window.clicks = 0</script>
            """
        )
        action = EnrollAction()
        locator = page.locator("button.btn-cart-enrol")
        action.click_once(locator)
        self.assertEqual(page.evaluate("window.clicks"), 1)
        with self.assertRaises(DuplicateClickPrevented):
            action.click_once(locator)
        self.assertEqual(page.evaluate("window.clicks"), 1)
        page.close()

    def test_disabled_button_is_not_force_clicked_or_retried(self) -> None:
        page = self.browser.new_page()
        page.set_content(
            """
            <button class="btn-cart-enrol" disabled
              onclick="window.clicks += 1">Enrol</button>
            <script>window.clicks = 0</script>
            """
        )
        action = EnrollAction()
        locator = page.locator("button.btn-cart-enrol")
        with self.assertRaises(PlaywrightTimeoutError):
            action.click_once(locator, timeout_ms=100)
        self.assertEqual(page.evaluate("window.clicks"), 0)
        with self.assertRaises(DuplicateClickPrevented):
            action.click_once(locator, timeout_ms=100)
        self.assertEqual(page.evaluate("window.clicks"), 0)
        page.close()


if __name__ == "__main__":
    unittest.main()
