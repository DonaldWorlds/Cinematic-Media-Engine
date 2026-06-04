"""Rich progress helpers for long-running pipeline steps.

``stage_progress`` and ``clip_progress`` each yield an ``on_event(name, event)``
callable wired to the scheduler (``utils.parallel.run_graph``). Both are thread-safe:
scheduler events arrive from worker threads, and updates are guarded by a lock on top
of Rich's own internal locking.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)


def make_progress() -> Progress:
    """Construct a configured Rich Progress for pipeline steps."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
    )


@contextmanager
def stage_progress() -> Iterator[Callable[[str, str], None]]:
    """Analysis progress: one row per stage with a live elapsed timer.

    Rows are created lazily on each stage's ``start`` event and frozen (✓/✗) on
    ``done``/``error`` — so a hung stage stays visibly running with a ticking timer
    (the 5h-stall diagnostic). One flat set of rows; no nested progress.
    """
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
    )
    tasks: dict[str, TaskID] = {}
    lock = threading.Lock()

    def on_event(name: str, event: str) -> None:
        with lock:
            if event == "start":
                tasks[name] = progress.add_task(f"analyze: {name}", total=None)
            elif name in tasks:
                mark = "[green]✓[/green]" if event == "done" else "[red]✗[/red]"
                progress.update(tasks[name], description=f"{mark} {name}")
                progress.stop_task(tasks[name])

    with progress:
        yield on_event


@contextmanager
def clip_progress(total: int) -> Iterator[Callable[[str, str], None]]:
    """Render progress: a single counter bar advancing on each clip ``done``."""
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
    )
    lock = threading.Lock()

    with progress:
        task = progress.add_task("render clips", total=total)

        def on_event(name: str, event: str) -> None:
            if event == "done":
                with lock:
                    progress.advance(task)

        yield on_event
