"""Phase 6 — task-graph scheduler tests (written before implementation).

Proves the producer/consumer scheduler that backs parallel analysis + render:
dependency ordering, genuine concurrency (via a Barrier — a serialized impl would
deadlock it), per-lane concurrency caps (via a live counter), and error isolation
with dependent-skip (SPEC §9). Threads-only executor per the CP1.5 decision.
"""

from __future__ import annotations

import threading
import time


def test_dependencies_run_in_order() -> None:
    from src.utils.parallel import Task, run_graph

    log: list[tuple[str, str]] = []
    lock = threading.Lock()

    def make(name: str):
        def fn() -> str:
            with lock:
                log.append((name, "start"))
            time.sleep(0.02)
            with lock:
                log.append((name, "end"))
            return name

        return fn

    tasks = [
        Task(name="A", fn=make("A")),
        Task(name="B", fn=make("B"), deps={"A"}),
    ]
    run_graph(tasks, max_workers=4, lane_limits={})
    assert log.index(("A", "end")) < log.index(("B", "start"))


def test_independent_tasks_run_concurrently() -> None:
    from src.utils.parallel import Task, run_graph

    # 3-party barrier with a timeout: if the scheduler serialized these, the
    # barrier never reaches 3 and wait() raises BrokenBarrierError -> test fails.
    barrier = threading.Barrier(3, timeout=5)

    def fn() -> bool:
        barrier.wait()
        return True

    tasks = [Task(name=f"t{i}", fn=fn, lane="light") for i in range(3)]
    results = run_graph(tasks, max_workers=3, lane_limits={"light": 3})
    assert all(results[f"t{i}"].value is True for i in range(3))


def test_lane_limit_caps_concurrency_at_one() -> None:
    from src.utils.parallel import Task, run_graph

    current = 0
    observed_max = 0
    lock = threading.Lock()

    def fn() -> None:
        nonlocal current, observed_max
        with lock:
            current += 1
            observed_max = max(observed_max, current)
        time.sleep(0.03)
        with lock:
            current -= 1

    tasks = [Task(name=f"h{i}", fn=fn, lane="heavy") for i in range(4)]
    run_graph(tasks, max_workers=4, lane_limits={"heavy": 1})
    assert observed_max == 1


def test_lane_limit_two_allows_two() -> None:
    from src.utils.parallel import Task, run_graph

    current = 0
    observed_max = 0
    lock = threading.Lock()

    def fn() -> None:
        nonlocal current, observed_max
        with lock:
            current += 1
            observed_max = max(observed_max, current)
        time.sleep(0.03)
        with lock:
            current -= 1

    tasks = [Task(name=f"r{i}", fn=fn, lane="render") for i in range(4)]
    run_graph(tasks, max_workers=4, lane_limits={"render": 2})
    assert observed_max == 2


def test_error_in_one_task_isolated() -> None:
    from src.utils.parallel import Task, run_graph

    def boom() -> None:
        raise ValueError("kaboom")

    tasks = [Task(name="X", fn=boom), Task(name="Y", fn=lambda: 42)]
    results = run_graph(tasks, max_workers=2, lane_limits={})
    assert results["Y"].value == 42
    assert isinstance(results["X"].error, ValueError)


def test_dependent_of_failed_task_is_skipped() -> None:
    from src.utils.parallel import Task, run_graph

    ran = {"B": False}

    def boom() -> None:
        raise RuntimeError("fail A")

    def b() -> None:
        ran["B"] = True

    tasks = [Task(name="A", fn=boom), Task(name="B", fn=b, deps={"A"})]
    results = run_graph(tasks, max_workers=2, lane_limits={})
    assert results["B"].skipped is True
    assert ran["B"] is False


def test_results_keyed_by_name_regardless_of_finish_order() -> None:
    from src.utils.parallel import Task, run_graph

    def make(name: str, delay: float):
        def fn() -> str:
            time.sleep(delay)
            return name

        return fn

    tasks = [Task(name=f"t{i}", fn=make(f"t{i}", 0.05 - i * 0.02), lane="x") for i in range(3)]
    results = run_graph(tasks, max_workers=3, lane_limits={"x": 3})
    assert {k: v.value for k, v in results.items()} == {"t0": "t0", "t1": "t1", "t2": "t2"}


def test_on_event_lifecycle() -> None:
    from src.utils.parallel import Task, run_graph

    events: list[tuple[str, str]] = []
    lock = threading.Lock()

    def record(name: str, event: str) -> None:
        with lock:
            events.append((name, event))

    def boom() -> None:
        raise ValueError()

    tasks = [Task(name="A", fn=lambda: 1), Task(name="B", fn=boom)]
    run_graph(tasks, max_workers=2, lane_limits={}, on_event=record)
    assert events.index(("A", "start")) < events.index(("A", "done"))
    assert ("B", "start") in events
    assert ("B", "error") in events
