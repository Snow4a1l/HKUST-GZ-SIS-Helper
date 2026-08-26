from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gui import update_config_file


CONFIG = """
target_datetime: "2026-09-01T15:30:00+08:00"
timezone: "Asia/Shanghai"
sis_url: "https://sisn.hkust-gz.edu.cn/"
shopping_cart_url: "https://sisn.hkust-gz.edu.cn/student/student-my-cart"
browser_channel: "msedge"
user_data_dir: ".private/browser-profile"
enroll_button_selector: "button.btn-cart-enrol"
select_all_checkbox_selector: 'div.mine-select input.el-checkbox__original[type="checkbox"][value="Select All"]'
select_all_click_target_selector: 'div.mine-select label.el-checkbox:has(input.el-checkbox__original[type="checkbox"][value="Select All"])'
expected_button_texts: ["Enrol", "Enroll"]
enable_pre_refresh: true
refresh_before_seconds: 2
manual_login_timeout_seconds: 1800
click_confirmation_button: false
confirmation_button_selector: ""
screenshot_dir: "var/screenshots"
log_file: "var/enroll-click.log"
lock_file: ".private/enroll-click.lock"
"""


class GuiConfigTests(unittest.TestCase):
    def test_gui_update_preserves_verified_selector(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(CONFIG, encoding="utf-8")
            settings = update_config_file(
                path,
                {
                    "target_datetime": "2026-09-02T16:00:00+08:00",
                    "refresh_before_seconds": 3,
                    "keep_browser_open_after_run": True,
                    "require_arm_phrase_confirmation": False,
                },
            )
            self.assertEqual(settings.target_datetime, "2026-09-02T16:00:00+08:00")
            self.assertEqual(settings.enroll_button_selector, "button.btn-cart-enrol")
            self.assertIn("value=\"Select All\"", settings.select_all_checkbox_selector)
            self.assertTrue(settings.keep_browser_open_after_run)
            self.assertFalse(settings.require_arm_phrase_confirmation)


if __name__ == "__main__":
    unittest.main()
