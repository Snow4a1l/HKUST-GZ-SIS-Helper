from __future__ import annotations

import unittest

from playwright.sync_api import sync_playwright

from cart_selection import CartSelectionAction
from errors import SafetyStop
from page_guard import PageGuard


SELECT_ALL_INPUT = (
    'div.mine-select input.el-checkbox__original[type="checkbox"]'
    '[value="Select All"]'
)
SELECT_ALL_CLICK = (
    'div.mine-select label.el-checkbox:has('
    'input.el-checkbox__original[type="checkbox"][value="Select All"])'
)


class CartSelectionTests(unittest.TestCase):
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

    def setUp(self) -> None:
        self.page = self.browser.new_page()
        self.guard = PageGuard(
            "button.btn-cart-enrol",
            ("Enrol", "Enroll"),
            "sisn.hkust-gz.edu.cn",
            SELECT_ALL_INPUT,
            SELECT_ALL_CLICK,
        )

    def tearDown(self) -> None:
        self.page.close()

    def _set_cart(self, *, prevent_selection: bool = False) -> None:
        prevent = "event.preventDefault();" if prevent_selection else ""
        self.page.set_content(
            f"""
            <div class="mine-select">
              <label class="el-checkbox" onclick="window.clicks += 1; {prevent}">
                <input class="el-checkbox__original" type="checkbox"
                  value="Select All">
                <span>Select All</span>
              </label>
            </div>
            <button class="btn-cart-enrol">Enrol</button>
            <script>window.clicks = 0;</script>
            """
        )

    def test_select_all_is_clicked_once_then_not_repeated(self) -> None:
        self._set_cart()
        action = CartSelectionAction()
        first = action.ensure_selected_once(self.page, self.guard)
        second = action.ensure_selected_once(self.page, self.guard)
        self.assertTrue(first.clicked)
        self.assertTrue(first.after.checked)
        self.assertFalse(second.clicked)
        self.assertEqual(self.page.evaluate("window.clicks"), 1)

    def test_failed_select_all_attempt_is_never_retried(self) -> None:
        self._set_cart(prevent_selection=True)
        action = CartSelectionAction()
        with self.assertRaises(SafetyStop):
            action.ensure_selected_once(self.page, self.guard)
        with self.assertRaises(SafetyStop):
            action.ensure_selected_once(self.page, self.guard)
        self.assertEqual(self.page.evaluate("window.clicks"), 1)


if __name__ == "__main__":
    unittest.main()
