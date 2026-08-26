"""Page, hostname, blocked-state, and unique-button safety checks."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import Locator, Page

from errors import SafetyStop


BLOCKED_MARKERS = (
    "system maintenance notice",
    "undergoing maintenance",
    "captcha",
    "verify you are human",
    "waiting room",
    "you are in line",
    "queue-it",
    "access denied",
    "temporarily blocked",
    "too many requests",
)
EMPTY_CART_MARKER = "your enrollment shopping cart is empty"
LOADING_MARKERS = ("loading...", "loading…")

COURSE_LINE_PATTERN = re.compile(
    r"\b(?P<subject>[A-Z]{2,8})\s*(?P<number>\d{4}[A-Z]?)\b"
    r"\s*[-\u2013\u2014:]\s*(?P<title>.+)"
)
COURSE_CODE_PATTERN = re.compile(r"\b(?P<subject>[A-Z]{2,8})\s*(?P<number>\d{4}[A-Z]?)\b")
SECTION_PATTERN = re.compile(
    r"\b(?P<section>[A-Z]{1,4}\d{1,3})\s*\((?P<class_number>\d{2,8})\)"
)


def sanitize_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def extract_cart_summaries(visible_text: str) -> list[str]:
    """Extract non-sensitive course and section labels from visible cart text.

    SIS renders the cart with nested UI components, so a course label is not
    guaranteed to be the first text in an element. Parsing the rendered text
    line-by-line avoids depending on an unverified wrapper element.
    """

    courses: list[str] = []
    sections: list[str] = []
    course_indexes: dict[str, int] = {}
    seen_sections: set[str] = set()

    for raw_line in visible_text.splitlines():
        # Browser innerText commonly separates table cells with tabs. Long
        # runs of spaces are also treated as column boundaries.
        for raw_part in re.split(r"\t+| {3,}", raw_line):
            part = re.sub(r"\s+", " ", raw_part).strip()
            if not part:
                continue

            course_match = COURSE_LINE_PATTERN.search(part)
            if course_match:
                code = (
                    f"{course_match.group('subject')}"
                    f"{course_match.group('number')}"
                )
                title = course_match.group("title").strip(" |\u2022\u2502")
                # Do not swallow unrelated page content if a component emits
                # one abnormally long line.
                title = title[:180].rstrip()
                summary = f"\u8bfe\u7a0b\uff1a{code} \u2014 {title}"
                code_key = code.casefold()
                if title and code_key not in course_indexes:
                    course_indexes[code_key] = len(courses)
                    courses.append(summary)
                elif title:
                    courses[course_indexes[code_key]] = summary
            else:
                # Some SIS component versions split the course code and title
                # into separate rendered lines. A code-only summary is still a
                # reliable non-empty-cart signal and is preferable to a false
                # "empty" result.
                code_match = COURSE_CODE_PATTERN.search(part)
                if code_match:
                    code = (
                        f"{code_match.group('subject')}"
                        f"{code_match.group('number')}"
                    )
                    summary = f"课程：{code}"
                    code_key = code.casefold()
                    if code_key not in course_indexes:
                        course_indexes[code_key] = len(courses)
                        courses.append(summary)

            for section_match in SECTION_PATTERN.finditer(part):
                section = (
                    f"Section\uff1a{section_match.group('section')} "
                    f"({section_match.group('class_number')})"
                )
                key = section.casefold()
                if key not in seen_sections:
                    seen_sections.add(key)
                    sections.append(section)

    return (courses + sections)[:40]


@dataclass(frozen=True)
class ButtonSnapshot:
    count: int
    tag: str | None = None
    visible_text: str | None = None
    aria_label: str | None = None
    visible: bool = False
    enabled: bool = False
    disabled_attribute: bool = False
    aria_disabled: str | None = None
    class_name: str | None = None

    def description(self) -> str:
        return (
            f"count={self.count} tag={self.tag!r} text={self.visible_text!r} "
            f"aria_label={self.aria_label!r} visible={self.visible} "
            f"enabled={self.enabled} disabled_attribute={self.disabled_attribute} "
            f"aria_disabled={self.aria_disabled!r} class={self.class_name!r}"
        )


@dataclass(frozen=True)
class SelectionSnapshot:
    count: int
    click_target_count: int = 0
    tag: str | None = None
    input_type: str | None = None
    value: str | None = None
    visible: bool = False
    enabled: bool = False
    checked: bool = False
    click_target_visible: bool = False
    class_name: str | None = None

    def description(self) -> str:
        return (
            f"count={self.count} click_target_count={self.click_target_count} "
            f"tag={self.tag!r} type={self.input_type!r} "
            f"value={self.value!r} visible={self.visible} enabled={self.enabled} "
            f"checked={self.checked} click_target_visible={self.click_target_visible} "
            f"class={self.class_name!r}"
        )


@dataclass(frozen=True)
class CartState:
    status: str
    summaries: tuple[str, ...]
    loading_visible: bool
    empty_marker_visible: bool

    def description(self) -> str:
        return (
            f"status={self.status} loading_visible={self.loading_visible} "
            f"empty_marker_visible={self.empty_marker_visible} "
            f"summary_count={len(self.summaries)}"
        )


class PageGuard:
    def __init__(
        self,
        selector: str,
        expected_texts: tuple[str, ...],
        expected_host: str,
        select_all_selector: str,
        select_all_click_selector: str,
    ) -> None:
        self.selector = selector
        self.expected_texts = {normalize_text(text) for text in expected_texts}
        self.expected_host = expected_host
        self.select_all_selector = select_all_selector
        self.select_all_click_selector = select_all_click_selector

    def locator(self, page: Page) -> Locator:
        return page.locator(self.selector)

    def select_all_locator(self, page: Page) -> Locator:
        return page.locator(self.select_all_selector)

    def select_all_click_locator(self, page: Page) -> Locator:
        return page.locator(self.select_all_click_selector)

    def validate_hostname(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != self.expected_host:
            raise SafetyStop(
                f"Unexpected page origin: {sanitize_url(url)}; execution stopped"
            )

    def validate_page(self, page: Page) -> None:
        self.validate_hostname(page.url)
        body = normalize_text(page.locator("body").inner_text(timeout=2_000))
        markers = [marker for marker in BLOCKED_MARKERS if marker in body]
        if markers:
            raise SafetyStop(
                "Blocked/security/maintenance page detected: " + ", ".join(markers)
            )
        path = urlsplit(page.url).path.rstrip("/")
        if path != "/student/student-my-cart":
            raise SafetyStop(f"Not on verified Shopping Cart path: {path}")

    def is_cart_empty(self, page: Page) -> bool:
        return self.inspect_cart_state(page).status == "empty"

    def validate_cart_has_items(self, page: Page) -> None:
        state = self.inspect_cart_state(page)
        if state.status == "loading":
            raise SafetyStop("Shopping Cart is still loading; execution stopped")
        if state.status == "empty":
            raise SafetyStop("Shopping Cart is empty; no selection or Enrol click allowed")
        if state.status != "ready":
            raise SafetyStop(
                "Shopping Cart contents could not be determined; execution stopped"
            )

    def inspect_cart_state(self, page: Page) -> CartState:
        """Classify the currently rendered cart without issuing a request."""

        visible_text = page.locator("body").inner_text(timeout=2_000)
        normalized = normalize_text(visible_text)
        loading_visible = any(marker in normalized for marker in LOADING_MARKERS)
        summaries = tuple(extract_cart_summaries(visible_text))
        has_course = any(item.startswith("课程：") for item in summaries)
        empty_visible = EMPTY_CART_MARKER in normalized
        if loading_visible:
            status = "loading"
        elif has_course:
            status = "ready"
        elif empty_visible:
            status = "empty"
        else:
            status = "unknown"
        return CartState(
            status=status,
            summaries=summaries,
            loading_visible=loading_visible,
            empty_marker_visible=empty_visible,
        )

    def wait_for_cart_state(
        self,
        page: Page,
        *,
        timeout_ms: float = 30_000,
        poll_interval: float = 0.25,
        stable_ms: float = 750,
        stop_requested: Callable[[], bool] | None = None,
    ) -> CartState:
        """Wait for a stable ready/empty SPA state using DOM reads only."""

        deadline = time.monotonic() + timeout_ms / 1000
        stable_key: tuple[str, tuple[str, ...]] | None = None
        stable_since = 0.0
        last_state: CartState | None = None
        while time.monotonic() < deadline:
            if stop_requested is not None and stop_requested():
                raise SafetyStop("Stopped by user while waiting for Shopping Cart")
            last_state = self.inspect_cart_state(page)
            if last_state.status in {"ready", "empty"}:
                key = (last_state.status, last_state.summaries)
                now = time.monotonic()
                if key != stable_key:
                    stable_key = key
                    stable_since = now
                elif (now - stable_since) * 1000 >= stable_ms:
                    return last_state
            else:
                stable_key = None
                stable_since = 0.0
            time.sleep(poll_interval)
        detail = last_state.description() if last_state else "no DOM observation"
        raise SafetyStop(
            f"Shopping Cart did not reach a stable loaded state within "
            f"{timeout_ms / 1000:g}s ({detail})"
        )

    def inspect_button(self, page: Page) -> ButtonSnapshot:
        locator = self.locator(page)
        count = locator.count()
        if count != 1:
            return ButtonSnapshot(count=count)
        button = locator.first
        details = button.evaluate(
            r"""el => ({
              tag: el.tagName.toLowerCase(),
              text: (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim(),
              ariaLabel: el.getAttribute('aria-label'),
              disabledAttribute: el.hasAttribute('disabled'),
              ariaDisabled: el.getAttribute('aria-disabled'),
              className: typeof el.className === 'string' ? el.className : null
            })"""
        )
        return ButtonSnapshot(
            count=count,
            tag=details["tag"],
            visible_text=details["text"],
            aria_label=details["ariaLabel"],
            visible=button.is_visible(),
            enabled=button.is_enabled(),
            disabled_attribute=bool(details["disabledAttribute"]),
            aria_disabled=details["ariaDisabled"],
            class_name=details["className"],
        )

    def validate_button(
        self, snapshot: ButtonSnapshot, *, require_enabled: bool
    ) -> None:
        if snapshot.count != 1:
            raise SafetyStop(
                f"Enroll locator must match exactly one element; got {snapshot.count}"
            )
        if snapshot.tag != "button":
            raise SafetyStop(f"Verified locator no longer points to a button: {snapshot.tag}")
        names = {
            normalize_text(snapshot.visible_text),
            normalize_text(snapshot.aria_label),
        }
        if not (names & self.expected_texts):
            raise SafetyStop(
                f"Button visible/accessibility text is unexpected: "
                f"{snapshot.visible_text!r}/{snapshot.aria_label!r}"
            )
        if not snapshot.visible:
            raise SafetyStop("Enroll button is not visible")
        if require_enabled and not snapshot.enabled:
            raise SafetyStop("Enroll button is not enabled")

    def course_summaries(self, page: Page) -> list[str]:
        """Return small visible course labels without modifying cart selections."""

        visible_text = page.locator("body").inner_text(timeout=3_000)
        return extract_cart_summaries(visible_text)

    def inspect_select_all(self, page: Page) -> SelectionSnapshot:
        locator = self.select_all_locator(page)
        count = locator.count()
        click_target = self.select_all_click_locator(page)
        click_target_count = click_target.count()
        if count != 1:
            return SelectionSnapshot(
                count=count, click_target_count=click_target_count
            )
        checkbox = locator.first
        details = checkbox.evaluate(
            r"""el => ({
              tag: el.tagName.toLowerCase(),
              inputType: el.getAttribute('type'),
              value: el.getAttribute('value'),
              className: typeof el.className === 'string' ? el.className : null
            })"""
        )
        return SelectionSnapshot(
            count=count,
            click_target_count=click_target_count,
            tag=details["tag"],
            input_type=details["inputType"],
            value=details["value"],
            visible=checkbox.is_visible(),
            enabled=checkbox.is_enabled(),
            checked=checkbox.is_checked(),
            click_target_visible=(
                click_target.first.is_visible() if click_target_count == 1 else False
            ),
            class_name=details["className"],
        )

    def validate_select_all(
        self, snapshot: SelectionSnapshot, *, require_checked: bool
    ) -> None:
        if snapshot.count != 1:
            raise SafetyStop(
                "Select All locator must match exactly one element; "
                f"got {snapshot.count}"
            )
        if snapshot.click_target_count != 1:
            raise SafetyStop(
                "Select All click target must match exactly one element; "
                f"got {snapshot.click_target_count}"
            )
        if snapshot.tag != "input" or normalize_text(snapshot.input_type) != "checkbox":
            raise SafetyStop(
                "Verified Select All locator no longer points to a checkbox input"
            )
        if normalize_text(snapshot.value) != "select all":
            raise SafetyStop(
                f"Select All checkbox value is unexpected: {snapshot.value!r}"
            )
        if not snapshot.enabled:
            raise SafetyStop("Select All checkbox is disabled")
        if not snapshot.click_target_visible:
            raise SafetyStop("Select All click target is not visible")
        if require_checked and not snapshot.checked:
            raise SafetyStop("Select All is no longer checked; Enrol click refused")
