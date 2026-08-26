"""Asia/Shanghai-aware target parsing and countdown scheduling."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from errors import ConfigError, SafetyStop


@dataclass(frozen=True)
class TargetSchedule:
    target: datetime
    timezone: ZoneInfo

    @classmethod
    def parse(cls, value: str, timezone_name: str) -> "TargetSchedule":
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(f"Unknown timezone: {timezone_name}") from exc
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ConfigError(f"Invalid target_datetime: {value}") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ConfigError("target_datetime must contain an explicit UTC offset")
        target = parsed.astimezone(timezone)
        return cls(target=target, timezone=timezone)

    def now(self) -> datetime:
        return datetime.now(self.timezone)

    def seconds_remaining(self, now: datetime | None = None) -> float:
        current = now or self.now()
        return (self.target - current).total_seconds()

    def ensure_future(self, now: datetime | None = None) -> None:
        if self.seconds_remaining(now) <= 0:
            raise SafetyStop("Target time has already passed; armed execution refused")


def wait_until(
    target: datetime,
    timezone: ZoneInfo,
    *,
    label: str,
    tick_interval: float = 0.2,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> None:
    """Display a local countdown without issuing any browser/network request."""

    last_second: int | None = None
    while True:
        if stop_requested is not None and stop_requested():
            raise SafetyStop("Stopped by user before the click")
        now = datetime.now(timezone)
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            break
        second = max(0, int(remaining))
        if second != last_second:
            progress = {
                "label": label,
                "now": now.isoformat(timespec="seconds"),
                "target": target.isoformat(timespec="seconds"),
                "remaining_seconds": remaining,
            }
            if progress_callback is not None:
                progress_callback(progress)
            print(
                f"\r{label} | now={now.isoformat(timespec='seconds')} "
                f"target={target.isoformat(timespec='seconds')} "
                f"remaining={remaining:.1f}s   ",
                end="",
                flush=True,
            )
            last_second = second
        time.sleep(min(tick_interval, remaining))
    print()
    if progress_callback is not None:
        progress_callback(
            {
                "label": label,
                "now": datetime.now(timezone).isoformat(timespec="seconds"),
                "target": target.isoformat(timespec="seconds"),
                "remaining_seconds": 0.0,
            }
        )
