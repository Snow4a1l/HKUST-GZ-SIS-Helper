from __future__ import annotations

import unittest

from playwright.sync_api import sync_playwright

from errors import SafetyStop
from page_guard import PageGuard, extract_cart_summaries


SELECT_ALL_INPUT = (
    'div.mine-select input.el-checkbox__original[type="checkbox"]'
    '[value="Select All"]'
)
SELECT_ALL_CLICK = (
    'div.mine-select label.el-checkbox:has('
    'input.el-checkbox__original[type="checkbox"][value="Select All"])'
)


class PageGuardTests(unittest.TestCase):
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

    def test_domain_check(self) -> None:
        self.guard.validate_hostname(
            "https://sisn.hkust-gz.edu.cn/student/student-my-cart"
        )
        with self.assertRaises(SafetyStop):
            self.guard.validate_hostname("https://example.com/student/student-my-cart")

    def test_unique_simulated_button(self) -> None:
        self.page.set_content(
            '<button class="btn-cart-enrol" type="button">Enrol</button>'
        )
        snapshot = self.guard.inspect_button(self.page)
        self.assertEqual(snapshot.count, 1)
        self.assertTrue(snapshot.enabled)
        self.guard.validate_button(snapshot, require_enabled=True)

    def test_duplicate_simulated_buttons_are_refused(self) -> None:
        self.page.set_content(
            '<button class="btn-cart-enrol">Enrol</button>'
            '<button class="btn-cart-enrol">Enrol</button>'
        )
        snapshot = self.guard.inspect_button(self.page)
        self.assertEqual(snapshot.count, 2)
        with self.assertRaises(SafetyStop):
            self.guard.validate_button(snapshot, require_enabled=True)

    def test_disabled_simulated_button_is_refused_for_click(self) -> None:
        self.page.set_content(
            '<button class="btn-cart-enrol" disabled aria-disabled="true">'
            "Enrol</button>"
        )
        snapshot = self.guard.inspect_button(self.page)
        with self.assertRaises(SafetyStop):
            self.guard.validate_button(snapshot, require_enabled=True)

    def test_verified_dom_shape_ignores_enrol_table_header(self) -> None:
        self.page.set_content(
            """
            <table><thead><tr><th><div class="cell">Enrol</div></th></tr></thead></table>
            <button aria-disabled="true" disabled type="button"
              class="el-button el-button--primary is-disabled is-plain btn-cart-enrol">
              <span><i class="iconfont iconicon_en mr-5"></i> Enrol</span>
            </button>
            """
        )
        self.assertEqual(self.page.get_by_text("Enrol", exact=True).count(), 2)
        snapshot = self.guard.inspect_button(self.page)
        self.assertEqual(snapshot.count, 1)
        self.assertEqual(snapshot.tag, "button")
        self.assertEqual(snapshot.visible_text, "Enrol")
        self.guard.validate_button(snapshot, require_enabled=False)

    def test_cart_summary_accepts_leading_icons_and_finds_section(self) -> None:
        visible_text = """
        Shopping Cart
        | MOES1006 - Contemporary Chinese Society and Thought I (3 Credits)
        Section    Session    Date & Time
        L03 (6975)    UG    01-SEP-2026 - 07-DEC-2026
        MOES1006 - Contemporary Chinese Society and Thought I (3 Credits)
        """
        self.assertEqual(
            extract_cart_summaries(visible_text),
            [
                "课程：MOES1006 — Contemporary Chinese Society and Thought I (3 Credits)",
                "Section：L03 (6975)",
            ],
        )

    def test_cart_summary_reads_rendered_html_without_clicking(self) -> None:
        self.page.set_content(
            """
            <div><span aria-hidden="true">|</span>
              <span>COMP2011 – Programming with C++ (4 Credits)</span>
            </div>
            <table><tr><td>T02 (12345)</td></tr></table>
            """
        )
        self.assertEqual(
            self.guard.course_summaries(self.page),
            [
                "课程：COMP2011 — Programming with C++ (4 Credits)",
                "Section：T02 (12345)",
            ],
        )

    def test_verified_select_all_dom_is_unique_and_read_only(self) -> None:
        self.page.set_content(
            """
            <div class="mine-select">
              <label class="el-checkbox el-checkbox--large">
                <span class="el-checkbox__input">
                  <input class="el-checkbox__original" type="checkbox"
                    aria-hidden="false" value="Select All">
                  <span class="el-checkbox__inner"></span>
                </span>
                <span class="el-checkbox__label">Select All</span>
              </label>
            </div>
            """
        )
        snapshot = self.guard.inspect_select_all(self.page)
        self.assertEqual(snapshot.count, 1)
        self.assertEqual(snapshot.click_target_count, 1)
        self.assertFalse(snapshot.checked)
        self.guard.validate_select_all(snapshot, require_checked=False)

    def test_duplicate_select_all_controls_are_refused(self) -> None:
        control = """
          <div class="mine-select"><label class="el-checkbox">
            <input class="el-checkbox__original" type="checkbox" value="Select All">
            Select All
          </label></div>
        """
        self.page.set_content(control + control)
        snapshot = self.guard.inspect_select_all(self.page)
        self.assertEqual(snapshot.count, 2)
        with self.assertRaises(SafetyStop):
            self.guard.validate_select_all(snapshot, require_checked=False)

    def test_empty_cart_marker_is_detected(self) -> None:
        self.page.set_content(
            "<main>Your enrollment shopping cart is empty. Go To Course Market</main>"
        )
        self.assertTrue(self.guard.is_cart_empty(self.page))
        with self.assertRaises(SafetyStop):
            self.guard.validate_cart_has_items(self.page)

    def test_loading_placeholder_is_not_treated_as_empty(self) -> None:
        self.page.set_content(
            """
            <main>Your enrollment shopping cart is empty. Go To Course Market</main>
            <div>Loading...</div>
            """
        )
        state = self.guard.inspect_cart_state(self.page)
        self.assertEqual(state.status, "loading")
        self.assertFalse(self.guard.is_cart_empty(self.page))

    def test_wait_for_cart_state_observes_async_course_render(self) -> None:
        self.page.set_content(
            """
            <main id="cart">Your enrollment shopping cart is empty.</main>
            <div id="loading">Loading...</div>
            <script>
              setTimeout(() => {
                document.querySelector('#loading').remove();
                document.querySelector('#cart').innerHTML =
                  '<div>COMP2011 - Programming with C++ (4 Credits)</div>' +
                  '<div>L01 (12345)</div>';
              }, 150);
            </script>
            """
        )
        state = self.guard.wait_for_cart_state(
            self.page,
            timeout_ms=2_000,
            poll_interval=0.05,
            stable_ms=100,
        )
        self.assertEqual(state.status, "ready")
        self.assertIn(
            "课程：COMP2011 — Programming with C++ (4 Credits)",
            state.summaries,
        )

    def test_hidden_empty_marker_does_not_override_visible_course(self) -> None:
        self.page.set_content(
            """
            <div style="display:none">Your enrollment shopping cart is empty.</div>
            <div>MOES1006</div>
            """
        )
        state = self.guard.inspect_cart_state(self.page)
        self.assertEqual(state.status, "ready")
        self.assertEqual(state.summaries, ("课程：MOES1006",))


if __name__ == "__main__":
    unittest.main()
