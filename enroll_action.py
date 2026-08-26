"""Single-attempt Enrol click primitive."""

from __future__ import annotations

from datetime import datetime

from playwright.sync_api import Locator

from errors import DuplicateClickPrevented


class EnrollAction:
    """Guarantee at most one Playwright click attempt per process."""

    def __init__(self) -> None:
        self._attempted = False

    @property
    def attempted(self) -> bool:
        return self._attempted

    def click_once(
        self, locator: Locator, *, timeout_ms: float = 5_000
    ) -> tuple[datetime, datetime]:
        if self._attempted:
            raise DuplicateClickPrevented("Second Enrol click prevented")
        self._attempted = True
        before = datetime.now().astimezone()
        # The attempted flag is set before Playwright is called. If click raises,
        # this method still refuses every retry.
        locator.click(timeout=timeout_ms)
        after = datetime.now().astimezone()
        return before, after
