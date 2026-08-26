"""Atomic single-instance lock file."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from errors import SafetyStop


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._held = False

    def __enter__(self) -> "InstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
            )
        except FileExistsError as exc:
            raise SafetyStop(
                f"Lock file already exists: {self.path}. Confirm no other instance "
                "is running before removing it manually."
            ) from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "pid": os.getpid(),
                    "started_at": datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    ),
                },
                handle,
            )
            handle.write("\n")
        self._held = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._held:
            try:
                self.path.unlink(missing_ok=True)
            finally:
                self._held = False

