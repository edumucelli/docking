from __future__ import annotations

from collections.abc import Callable

import pytest

from docking.applets.worker import BackgroundWorker


@pytest.mark.parametrize("failure_stage", ["construct", "start"])
def test_guarded_worker_can_retry_after_thread_startup_failure(failure_stage):
    attempts = 0
    delivered = []

    def thread_factory(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            if failure_stage == "construct":
                raise RuntimeError("thread creation failed")

            class FailingThread:
                def start(self):
                    raise RuntimeError("thread start failed")

            return FailingThread()
        return _ImmediateThread(**kwargs)

    worker = BackgroundWorker(
        idle_add=lambda callback, *args: callback(*args), thread_factory=thread_factory
    )
    with pytest.raises(RuntimeError):
        worker.run_guarded(key="refresh", name="refresh", fn=lambda: 1)

    assert worker.run_guarded(
        key="refresh", name="refresh", fn=lambda: 2, on_result=delivered.append
    )
    assert delivered == [2]


class _ImmediateThread:
    def __init__(self, target, daemon=True):
        _ = daemon
        self._target = target

    def start(self):
        self._target()


def test_run_delivers_result_via_idle_add():
    delivered: list[int] = []
    calls: list[tuple[object, tuple[object, ...]]] = []

    def idle_add(func, *args):
        calls.append((func, args))
        return func(*args)

    worker = BackgroundWorker(idle_add=idle_add, thread_factory=_ImmediateThread)
    worker.run(
        name="demo", fn=lambda: 7, on_result=lambda value: delivered.append(value)
    )

    assert delivered == [7]
    assert len(calls) == 1


def test_run_guarded_suppresses_duplicate_key_until_completion():
    delivered: list[int] = []
    pending: list[tuple[Callable[..., object], tuple[object, ...]]] = []

    def idle_add(func, *args):
        pending.append((func, args))
        return 1

    worker = BackgroundWorker(idle_add=idle_add, thread_factory=_ImmediateThread)

    assert (
        worker.run_guarded(
            key="refresh",
            name="demo-refresh",
            fn=lambda: 1,
            on_result=lambda value: delivered.append(value),
        )
        is True
    )
    assert (
        worker.run_guarded(
            key="refresh",
            name="demo-refresh",
            fn=lambda: 2,
            on_result=lambda value: delivered.append(value),
        )
        is False
    )

    func, args = pending.pop(0)
    func(*args)

    assert delivered == [1]
    assert (
        worker.run_guarded(
            key="refresh",
            name="demo-refresh",
            fn=lambda: 3,
            on_result=lambda value: delivered.append(value),
        )
        is True
    )


def test_run_guarded_delivers_errors_and_releases_key():
    delivered: list[str] = []
    pending: list[tuple[Callable[..., object], tuple[object, ...]]] = []

    def idle_add(func, *args):
        pending.append((func, args))
        return 1

    worker = BackgroundWorker(idle_add=idle_add, thread_factory=_ImmediateThread)

    assert (
        worker.run_guarded(
            key="refresh",
            name="demo-refresh",
            fn=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            on_error=lambda exc: delivered.append(str(exc)),
        )
        is True
    )

    func, args = pending.pop(0)
    func(*args)

    assert delivered == ["boom"]
    assert (
        worker.run_guarded(
            key="refresh",
            name="demo-refresh",
            fn=lambda: 5,
            on_result=lambda value: delivered.append(str(value)),
        )
        is True
    )
