"""File lock so a slow cycle and the next scheduled one never run at the same time."""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def _try_acquire(path: Path, stale_after_seconds: float) -> bool:
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age <= stale_after_seconds:
                return False
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w") as fh:
            fh.write(str(os.getpid()))
        return True
    return False


@contextmanager
def cycle_lock(path: Path, stale_after_seconds: float = 1800) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    acquired = _try_acquire(path, stale_after_seconds)
    try:
        yield acquired
    finally:
        if acquired:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
