"""Safe one-shot HKUST(GZ) SIS Shopping Cart scheduler."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from browser_session import BrowserSession
from cart_selection import CartSelectionAction
from config import ConfigError, Settings, load_settings
from enroll_action import EnrollAction
from errors import EnrollClickerError, SafetyStop
from lock_file import InstanceLock
from logging_setup import configure_logging
from page_guard import (
    ButtonSnapshot,
    CartState,
    PageGuard,
    SelectionSnapshot,
    sanitize_url,
)
from scheduler import TargetSchedule, wait_until


EventCallback = Callable[[str, object], None]
ConfirmationProvider = Callable[[dict[str, object]], str]
ENROL_CLICK_TIMEOUT_MS = 250


def emit(callback: EventCallback | None, kind: str, payload: object) -> None:
    if callback is not None:
        callback(kind, payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-shot scheduled SIS Shopping Cart Enrol clicker"
    )
    parser.add_argument(
        "--config", default=os.getenv("ENROLL_CONFIG", "config.yaml")
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Inspect only; never click")
    mode.add_argument("--arm", action="store_true", help="Allow one click after confirmation")
    return parser.parse_args()


def screenshot_path(settings: Settings, label: str) -> Path:
    settings.screenshot_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    return settings.screenshot_dir / f"{stamp}-{label}.png"


def build_guard(settings: Settings) -> PageGuard:
    return PageGuard(
        settings.enroll_button_selector,
        settings.expected_button_texts,
        "sisn.hkust-gz.edu.cn",
        settings.select_all_checkbox_selector,
        settings.select_all_click_target_selector,
    )


def emit_selection(
    callback: EventCallback | None,
    snapshot: SelectionSnapshot,
    *,
    phase: str,
    cart_empty: bool,
) -> None:
    emit(
        callback,
        "selection",
        {
            "phase": phase,
            "count": snapshot.count,
            "click_target_count": snapshot.click_target_count,
            "checked": snapshot.checked,
            "enabled": snapshot.enabled,
            "cart_empty": cart_empty,
        },
    )


def wait_for_stable_cart(
    settings: Settings,
    page,
    guard: PageGuard,
    logger,
    *,
    phase: str,
    require_items: bool,
    stop_event: threading.Event | None,
    event_callback: EventCallback | None,
) -> CartState:
    """Wait for the SPA cart payload to replace its loading placeholder."""

    try:
        state = guard.wait_for_cart_state(
            page,
            timeout_ms=30_000,
            stop_requested=(stop_event.is_set if stop_event else None),
        )
    except Exception:
        logger.exception("Shopping Cart load-state wait failed at phase=%s", phase)
        image = screenshot_path(settings, f"cart-state-timeout-{phase}")
        try:
            page.screenshot(path=str(image), full_page=True)
            logger.info("Cart-state failure screenshot: %s", image)
            emit(event_callback, "screenshot", str(image))
        except Exception:
            logger.exception("Could not save cart-state failure screenshot")
        raise

    logger.info("Stable Shopping Cart state phase=%s: %s", phase, state.description())
    emit(
        event_callback,
        "cart_state",
        {
            "phase": phase,
            "status": state.status,
            "summary_count": len(state.summaries),
        },
    )
    if require_items and state.status != "ready":
        image = screenshot_path(settings, f"cart-{state.status}-{phase}")
        try:
            page.screenshot(path=str(image), full_page=True)
            logger.info("Non-ready cart screenshot: %s", image)
            emit(event_callback, "screenshot", str(image))
        except Exception:
            logger.exception("Could not save non-ready cart screenshot")
        if state.status == "empty":
            raise SafetyStop(
                "Shopping Cart finished loading and is empty; no selection or "
                "Enrol click allowed"
            )
        raise SafetyStop(
            f"Shopping Cart finished in unexpected state: {state.status}"
        )
    return state


def ensure_cart_selected(
    settings: Settings,
    page,
    guard: PageGuard,
    action: CartSelectionAction,
    logger,
    *,
    phase: str,
    event_callback: EventCallback | None,
) -> SelectionSnapshot:
    """Select all once and record the result without touching Enrol."""

    guard.validate_page(page)
    guard.validate_cart_has_items(page)
    result = action.ensure_selected_once(page, guard, timeout_ms=5_000)
    if result.clicked:
        logger.info(
            "Select All clicked once at phase=%s before=%s after=%s started=%s "
            "finished=%s",
            phase,
            result.before.description(),
            result.after.description(),
            result.started_at.isoformat(timespec="milliseconds"),
            result.finished_at.isoformat(timespec="milliseconds"),
        )
        emit(event_callback, "status", "Select All checked once")
        selected_image = screenshot_path(settings, "after-select-all")
        page.screenshot(path=str(selected_image), full_page=True)
        logger.info("After-Select-All screenshot: %s", selected_image)
        emit(event_callback, "screenshot", str(selected_image))
    else:
        logger.info(
            "Select All already checked at phase=%s; no selection click needed: %s",
            phase,
            result.after.description(),
        )
        emit(event_callback, "status", "Select All already checked")
    emit_selection(
        event_callback,
        result.after,
        phase=phase,
        cart_empty=False,
    )
    button_after_selection = guard.inspect_button(page)
    guard.validate_button(button_after_selection, require_enabled=False)
    logger.info(
        "Enrol state after Select All phase=%s: %s",
        phase,
        button_after_selection.description(),
    )
    emit(event_callback, "button", button_after_selection.description())
    return result.after


def print_summary(settings: Settings, schedule: TargetSchedule, snapshot: ButtonSnapshot) -> None:
    print("\nExecution summary")
    print(f"  target:   {schedule.target.isoformat(timespec='seconds')}")
    print(f"  timezone: {settings.timezone}")
    print(f"  URL:      {sanitize_url(settings.shopping_cart_url)}")
    print(f"  Enrol:    {settings.enroll_button_selector}")
    print(f"  Select:   {settings.select_all_checkbox_selector}")
    print(f"  button:   {snapshot.description()}")
    print(
        "  scope:    one Select All if needed, then first Enrol only; "
        "no Finish Enrolling/Confirm/Submit click"
    )


def dry_run(
    settings: Settings,
    schedule: TargetSchedule,
    logger,
    *,
    stop_event: threading.Event | None = None,
    event_callback: EventCallback | None = None,
) -> int:
    emit(event_callback, "status", "Opening dedicated Edge for dry-run")
    with BrowserSession(settings, logger, stop_event=stop_event) as browser:
        browser.open_cart()
        page = browser.wait_for_manual_login_and_cart()
        guard = build_guard(settings)
        guard.validate_page(page)
        cart_state = wait_for_stable_cart(
            settings,
            page,
            guard,
            logger,
            phase="dry-run-startup",
            require_items=False,
            stop_event=stop_event,
            event_callback=event_callback,
        )
        snapshot = guard.inspect_button(page)
        guard.validate_button(snapshot, require_enabled=False)
        logger.info("Dry-run locator/button state: %s", snapshot.description())
        emit(event_callback, "button", snapshot.description())
        print_summary(settings, schedule, snapshot)
        cart_empty = cart_state.status == "empty"
        selection = guard.inspect_select_all(page)
        guard.validate_select_all(selection, require_checked=False)
        logger.info(
            "Dry-run Select All state cart_empty=%s: %s",
            cart_empty,
            selection.description(),
        )
        emit_selection(
            event_callback,
            selection,
            phase="dry-run",
            cart_empty=cart_empty,
        )
        summaries = list(cart_state.summaries)
        emit(event_callback, "courses", summaries)
        if summaries:
            print("\nShopping Cart course summary (read-only):")
            for item in summaries:
                print(f"  - {item}")
        image = screenshot_path(settings, "dry-run")
        page.screenshot(path=str(image), full_page=True)
        logger.info("Dry-run screenshot saved: %s", image)
        emit(event_callback, "screenshot", str(image))
        if cart_empty:
            emit(
                event_callback,
                "status",
                "Dry-run complete; Shopping Cart is empty; no click performed",
            )
        elif selection.checked:
            emit(
                event_callback,
                "status",
                "Dry-run complete; Select All is checked; no click performed",
            )
        else:
            emit(
                event_callback,
                "status",
                "Dry-run complete; Select All is unchecked; no click performed",
            )
        print(f"\nDRY RUN complete. No click performed. Screenshot: {image}")
        if settings.keep_browser_open_after_run:
            emit(
                event_callback,
                "status",
                "Dry-run complete; Shopping Cart kept open until Safe Stop/manual close",
            )
            browser.wait_for_user_release(page)
        return 0


def armed_run(
    settings: Settings,
    schedule: TargetSchedule,
    logger,
    *,
    confirmation_provider: ConfirmationProvider | None = None,
    stop_event: threading.Event | None = None,
    event_callback: EventCallback | None = None,
) -> int:
    schedule.ensure_future()

    emit(event_callback, "status", "Opening dedicated Edge for armed run")
    with BrowserSession(settings, logger, stop_event=stop_event) as browser:
        browser.open_cart()
        page = browser.wait_for_manual_login_and_cart()
        guard = build_guard(settings)
        guard.validate_page(page)
        startup_cart_state = wait_for_stable_cart(
            settings,
            page,
            guard,
            logger,
            phase="armed-startup",
            require_items=True,
            stop_event=stop_event,
            event_callback=event_callback,
        )
        snapshot = guard.inspect_button(page)
        guard.validate_button(snapshot, require_enabled=False)
        logger.info("Armed startup locator/button state: %s", snapshot.description())
        emit(event_callback, "button", snapshot.description())
        print_summary(settings, schedule, snapshot)

        startup_selection = guard.inspect_select_all(page)
        guard.validate_select_all(startup_selection, require_checked=False)
        logger.info(
            "Armed startup Select All state: %s",
            startup_selection.description(),
        )
        emit_selection(
            event_callback,
            startup_selection,
            phase="armed-startup",
            cart_empty=False,
        )

        summaries = list(startup_cart_state.summaries)
        emit(event_callback, "courses", summaries)
        if summaries:
            print("\nShopping Cart course summary (read-only):")
            for item in summaries:
                print(f"  - {item}")

        schedule.ensure_future()
        confirmation_context = {
            "target": schedule.target.isoformat(timespec="seconds"),
            "timezone": settings.timezone,
            "url": sanitize_url(settings.shopping_cart_url),
            "selector": settings.enroll_button_selector,
            "button": snapshot.description(),
            "courses": summaries,
        }
        if settings.require_arm_phrase_confirmation:
            if confirmation_provider is None:
                confirmation = input('\nType exactly "ARM ENROLL" to continue: ')
            else:
                confirmation = confirmation_provider(confirmation_context)
            if confirmation != "ARM ENROLL":
                raise SafetyStop("Arm confirmation did not match; no click performed")
            logger.info("User supplied exact ARM ENROLL confirmation")
        else:
            logger.info(
                "ARM ENROLL phrase confirmation disabled by configuration; "
                "the explicit Arm action is the authorization"
            )
        schedule.ensure_future()

        selection_action = CartSelectionAction()
        if not settings.enable_pre_refresh:
            ensure_cart_selected(
                settings,
                page,
                guard,
                selection_action,
                logger,
                phase="armed-startup",
                event_callback=event_callback,
            )
            schedule.ensure_future()

        if settings.enable_pre_refresh:
            refresh_at = schedule.target - timedelta(
                seconds=settings.refresh_before_seconds
            )
            if schedule.now() < refresh_at:
                wait_until(
                    refresh_at,
                    schedule.timezone,
                    label="Waiting for one pre-refresh",
                    progress_callback=lambda data: emit(
                        event_callback, "progress", data
                    ),
                    stop_requested=(stop_event.is_set if stop_event else None),
                )
            schedule.ensure_future()
            refreshed_at = schedule.now()
            logger.info(
                "Executing the single configured pre-refresh at %s",
                refreshed_at.isoformat(timespec="milliseconds"),
            )
            page.reload(wait_until="domcontentloaded", timeout=30_000)
            logger.info("Pre-refresh completed")
            emit(event_callback, "status", "Single pre-refresh completed")
            guard.validate_page(page)
            refreshed_cart_state = wait_for_stable_cart(
                settings,
                page,
                guard,
                logger,
                phase="after-pre-refresh",
                require_items=True,
                stop_event=stop_event,
                event_callback=event_callback,
            )
            emit(event_callback, "courses", list(refreshed_cart_state.summaries))
            refreshed_button = guard.inspect_button(page)
            guard.validate_button(refreshed_button, require_enabled=False)
            logger.info(
                "Button state after pre-refresh: %s",
                refreshed_button.description(),
            )
            emit(event_callback, "button", refreshed_button.description())
            ensure_cart_selected(
                settings,
                page,
                guard,
                selection_action,
                logger,
                phase="after-pre-refresh",
                event_callback=event_callback,
            )

        wait_until(
            schedule.target,
            schedule.timezone,
            label="Waiting for target",
            progress_callback=lambda data: emit(event_callback, "progress", data),
            stop_requested=(stop_event.is_set if stop_event else None),
        )

        if stop_event is not None and stop_event.is_set():
            raise SafetyStop("Stopped by user before the click")
        guard.validate_page(page)
        guard.validate_cart_has_items(page)
        target_selection = guard.inspect_select_all(page)
        guard.validate_select_all(target_selection, require_checked=True)
        logger.info(
            "Target Select All verification: %s",
            target_selection.description(),
        )
        emit_selection(
            event_callback,
            target_selection,
            phase="target",
            cart_empty=False,
        )
        snapshot = guard.inspect_button(page)
        guard.validate_button(snapshot, require_enabled=False)
        logger.info("Direct one-shot target check: %s", snapshot.description())
        before_image = screenshot_path(settings, "before-click")
        page.screenshot(path=str(before_image), full_page=True)
        logger.info("Before-click screenshot: %s", before_image)
        logger.info(
            "Immediate single click attempt starting at %s; technical "
            "actionability timeout=%sms",
            schedule.now().isoformat(timespec="milliseconds"),
            ENROL_CLICK_TIMEOUT_MS,
        )
        action = EnrollAction()
        try:
            before, after = action.click_once(
                guard.locator(page),
                timeout_ms=ENROL_CLICK_TIMEOUT_MS,
            )
        except Exception:
            logger.exception(
                "The single standard Playwright click attempt failed or timed out; "
                "retry is forbidden"
            )
            failure_image = screenshot_path(settings, "click-failed")
            try:
                page.screenshot(path=str(failure_image), full_page=True)
                logger.info("Failure screenshot: %s", failure_image)
                emit(event_callback, "screenshot", str(failure_image))
            except Exception:
                logger.exception("Could not save failure screenshot")
            if settings.keep_browser_open_after_run:
                emit(
                    event_callback,
                    "status",
                    "Click attempt failed/timed out; Shopping Cart kept open",
                )
                browser.wait_for_user_release(page)
            raise
        logger.info(
            "Single Enrol click returned; before=%s after=%s",
            before.isoformat(timespec="milliseconds"),
            after.isoformat(timespec="milliseconds"),
        )
        time.sleep(0.5)
        after_image = screenshot_path(settings, "after-click")
        after_image_saved = False
        try:
            page.screenshot(path=str(after_image), full_page=True)
            logger.info("After-click screenshot: %s", after_image)
            after_image_saved = True
        except Exception:
            logger.exception("Could not save after-click screenshot")
        print(
            "One Enrol click was attempted. No follow-up button will be "
            "clicked; finish manually if the site requests confirmation."
        )
        emit(
            event_callback,
            "status",
            "One Enrol click attempted; follow-up remains manual",
        )
        if after_image_saved:
            emit(event_callback, "screenshot", str(after_image))
        if settings.keep_browser_open_after_run:
            emit(
                event_callback,
                "status",
                "Shopping Cart kept open for manual follow-up; use Safe Stop when done",
            )
            browser.wait_for_user_release(page)
        return 0


def run_with_settings(
    settings: Settings,
    *,
    armed: bool,
    logger,
    confirmation_provider: ConfirmationProvider | None = None,
    stop_event: threading.Event | None = None,
    event_callback: EventCallback | None = None,
) -> int:
    """Run one CLI or GUI session with the same safety boundaries."""

    schedule = TargetSchedule.parse(settings.target_datetime, settings.timezone)
    mode = "ARMED" if armed else "DRY-RUN"
    logger.info(
        "Program start mode=%s target=%s timezone=%s URL=%s selector=%s",
        mode,
        schedule.target.isoformat(timespec="seconds"),
        settings.timezone,
        sanitize_url(settings.shopping_cart_url),
        settings.enroll_button_selector,
    )
    emit(event_callback, "mode", mode)
    with InstanceLock(settings.lock_file):
        if armed:
            return armed_run(
                settings,
                schedule,
                logger,
                confirmation_provider=confirmation_provider,
                stop_event=stop_event,
                event_callback=event_callback,
            )
        if schedule.seconds_remaining() <= 0:
            logger.warning("Configured target is past; allowed only because this is dry-run")
        return dry_run(
            settings,
            schedule,
            logger,
            stop_event=stop_event,
            event_callback=event_callback,
        )


def main() -> int:
    load_dotenv()
    args = parse_args()
    armed = bool(args.arm)
    try:
        settings = load_settings(Path(args.config))
        logger = configure_logging(settings.log_file)
        return run_with_settings(settings, armed=armed, logger=logger)
    except KeyboardInterrupt:
        try:
            logger.warning("Interrupted by user; execution stopped")
        except UnboundLocalError:
            pass
        print("Interrupted by user; no retry will be attempted.", file=sys.stderr)
        return 130
    except (ConfigError, EnrollClickerError) as exc:
        try:
            logger.error("Stopped safely: %s", exc)
        except UnboundLocalError:
            pass
        print(f"STOPPED: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        try:
            logger.exception("Unhandled exception; execution stopped")
        except UnboundLocalError:
            pass
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
