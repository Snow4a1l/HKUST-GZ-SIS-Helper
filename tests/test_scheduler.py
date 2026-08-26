from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from errors import SafetyStop
from scheduler import TargetSchedule, wait_until


class SchedulerTests(unittest.TestCase):
    def test_shanghai_target_and_remaining_time(self) -> None:
        schedule = TargetSchedule.parse(
            "2026-08-26T15:30:00+08:00", "Asia/Shanghai"
        )
        now = datetime.fromisoformat("2026-08-26T15:29:55+08:00")
        self.assertEqual(schedule.seconds_remaining(now), 5)

    def test_past_target_is_refused(self) -> None:
        schedule = TargetSchedule.parse(
            "2026-08-26T15:30:00+08:00", "Asia/Shanghai"
        )
        now = datetime.fromisoformat("2026-08-26T15:30:01+08:00")
        with self.assertRaises(SafetyStop):
            schedule.ensure_future(now)

    def test_gui_progress_callback_receives_countdown(self) -> None:
        timezone = ZoneInfo("Asia/Shanghai")
        events: list[dict[str, object]] = []
        wait_until(
            datetime.now(timezone) + timedelta(milliseconds=40),
            timezone,
            label="test",
            tick_interval=0.01,
            progress_callback=events.append,
        )
        self.assertTrue(events)
        self.assertEqual(events[-1]["remaining_seconds"], 0.0)

    def test_gui_stop_callback_refuses_to_continue(self) -> None:
        timezone = ZoneInfo("Asia/Shanghai")
        with self.assertRaises(SafetyStop):
            wait_until(
                datetime.now(timezone) + timedelta(seconds=1),
                timezone,
                label="test",
                stop_requested=lambda: True,
            )


if __name__ == "__main__":
    unittest.main()
