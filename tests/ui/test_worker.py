from __future__ import annotations

from collections.abc import Callable

from docking.applets.worker import BackgroundWorker


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
