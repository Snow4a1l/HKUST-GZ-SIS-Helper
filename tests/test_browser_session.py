from __future__ import annotations

import logging
import unittest

from playwright.sync_api import sync_playwright

from browser_session import BrowserSession


class BrowserSessionTests(unittest.TestCase):
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

    def test_only_cart_page_is_kept_in_dedicated_context(self) -> None:
        context = self.browser.new_context()
        extra_one = context.new_page()
        cart = context.new_page()
        extra_two = context.new_page()
        extra_one.set_content("<p>login</p>")
        cart.set_content("<p>cart</p>")
        extra_two.set_content("<p>blank</p>")

        session = BrowserSession.__new__(BrowserSession)
        session.context = context
        session.logger = logging.getLogger("browser-session-test")
        session.close_other_pages(cart)

        self.assertEqual(context.pages, [cart])
        context.close()


if __name__ == "__main__":
    unittest.main()

