"""One-shot Shopping Cart Select All action."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from playwright.sync_api import Page

from errors import SafetyStop
from page_guard import PageGuard, SelectionSnapshot


@dataclass(frozen=True)
class SelectionResult:
    before: SelectionSnapshot
    after: SelectionSnapshot
    clicked: bool
    started_at: datetime | None = None
    finished_at: datetime | None = None


class CartSelectionAction:
    """Select all at most once; mark the attempt before Playwright acts."""

    def __init__(self) -> None:
        self.attempted = False

    def ensure_selected_once(
        self,
        page: Page,
        guard: PageGuard,
        *,
        timeout_ms: float = 5_000,
    ) -> SelectionResult:
        before = guard.inspect_select_all(page)
        guard.validate_select_all(before, require_checked=False)
        if before.checked:
            return SelectionResult(before=before, after=before, clicked=False)
        if self.attempted:
            raise SafetyStop("Select All was already attempted; automatic retry refused")

        self.attempted = True
        started_at = datetime.now().astimezone()
        # One standard Playwright click on the verified visible label. The
        # underlying Element Plus input is visually hidden, so its separately
        # verified label is the correct non-coordinate click target.
        guard.select_all_click_locator(page).click(timeout=timeout_ms)
        finished_at = datetime.now().astimezone()

        after = guard.inspect_select_all(page)
        guard.validate_select_all(after, require_checked=True)
        return SelectionResult(
            before=before,
            after=after,
            clicked=True,
            started_at=started_at,
            finished_at=finished_at,
        )
