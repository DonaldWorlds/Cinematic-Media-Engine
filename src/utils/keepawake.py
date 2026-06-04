"""macOS sleep guard — keep the machine awake during long runs.

A multi-hour analysis/render can be suspended by macOS idle/display sleep (the
~5h mid-pipeline stall observed in Session 10). ``keep_awake()`` spawns
``caffeinate`` bound to our PID for the duration of the context and is a clean
no-op on any non-macOS platform.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from collections.abc import Iterator

from .logging import get_logger

_log = get_logger(__name__)


@contextlib.contextmanager
def keep_awake() -> Iterator[None]:
    """Inhibit system/display sleep while the context is active (macOS only)."""
    if sys.platform != "darwin":
        yield  # no-op off macOS
        return

    proc: subprocess.Popen[bytes] | None = None
    try:
        # -i idle, -d display, -m disk, -s system; -w <pid> auto-exits with us
        proc = subprocess.Popen(["caffeinate", "-i", "-d", "-m", "-s", "-w", str(os.getpid())])
        _log.info("caffeinate active — system sleep inhibited for this run")
    except FileNotFoundError:  # caffeinate absent -> degrade gracefully (SPEC §9)
        _log.warning("caffeinate not found; continuing without a sleep guard")
        proc = None

    try:
        yield
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            with contextlib.suppress(Exception):
                proc.wait(timeout=2)
