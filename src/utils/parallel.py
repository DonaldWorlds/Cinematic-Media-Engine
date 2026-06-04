"""A dependency-aware, lane-throttled task scheduler (threads-only).

Phase 6 parallelism runs on THREADS, not processes (CP1.5 decision): the heavy work
in this engine — cv2 decode, numpy/librosa, CTranslate2/torch, ffmpeg subprocesses —
releases the GIL during native compute, so threads give real overlap. A single process
also loads each native lib (and the cv2/av libavdevice dylibs) exactly once, which
sidesteps the multiprocessing-``spawn`` dylib clash and the per-worker memory blow-up
that would OOM an 8GB M1 (see KNOWLEDGE.md).

``run_graph`` submits a task only once its dependencies have completed successfully, so
no worker thread ever blocks waiting on another task — the scheduler is deadlock-free.
Each lane is throttled by a semaphore (e.g. one GB-class model at a time on 8GB), and a
task that raises is recorded while its dependents are skipped, without bringing down the
rest of the batch (SPEC §9).

Thread-safety: the coordinator bookkeeping (results / pending / in-flight) is touched
ONLY by the calling thread; worker threads just run a task's ``fn`` and return a
``TaskResult``. There is no shared mutable scheduler state across threads.
"""

from __future__ import annotations

import concurrent.futures
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Task:
    """One unit of work: a name, a zero-arg callable, its deps, and its lane."""

    name: str
    fn: Callable[[], Any]
    deps: set[str] = field(default_factory=set)
    lane: str = "default"


@dataclass
class TaskResult:
    """Outcome of a task: a value, a captured error, or skipped (a dep failed)."""

    value: Any = None
    error: Exception | None = None
    skipped: bool = False


def _run_one(task: Task, on_event: Callable[[str, str], None] | None) -> TaskResult:
    """Run one task on a worker thread; isolate any error into the result."""
    if on_event is not None:
        on_event(task.name, "start")
    try:
        value = task.fn()
    except Exception as exc:  # isolated: recorded, never re-raised into the batch
        if on_event is not None:
            on_event(task.name, "error")
        return TaskResult(error=exc)
    if on_event is not None:
        on_event(task.name, "done")
    return TaskResult(value=value)


def run_graph(
    tasks: list[Task],
    *,
    max_workers: int,
    lane_limits: Mapping[str, int] | None = None,
    on_event: Callable[[str, str], None] | None = None,
) -> dict[str, TaskResult]:
    """Run ``tasks`` honoring deps + per-lane concurrency caps; return results by name."""
    semaphores = {lane: threading.Semaphore(n) for lane, n in (lane_limits or {}).items()}
    pending: dict[str, Task] = {t.name: t for t in tasks}
    results: dict[str, TaskResult] = {}
    in_flight: dict[concurrent.futures.Future[TaskResult], tuple[str, str]] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        while pending or in_flight:
            progressed = False
            for name in list(pending):
                task = pending[name]
                if not all(dep in results for dep in task.deps):
                    continue  # dependencies not finished yet
                if any(results[d].error is not None or results[d].skipped for d in task.deps):
                    results[name] = TaskResult(skipped=True)  # a dependency failed -> skip
                    del pending[name]
                    progressed = True
                    continue
                sem = semaphores.get(task.lane)
                if sem is not None and not sem.acquire(blocking=False):
                    continue  # lane saturated; a running task will free a slot
                in_flight[executor.submit(_run_one, task, on_event)] = (name, task.lane)
                del pending[name]
                progressed = True

            if not in_flight:
                if pending and not progressed:  # cycle or dep on a missing task
                    raise ValueError(f"unresolvable task dependencies: {sorted(pending)}")
                continue

            done, _ = concurrent.futures.wait(
                in_flight, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for fut in done:
                name, lane = in_flight.pop(fut)
                results[name] = fut.result()
                sem = semaphores.get(lane)
                if sem is not None:
                    sem.release()

    return results
