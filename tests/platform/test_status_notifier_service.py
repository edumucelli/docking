"""Tests for the shared StatusNotifier poll service."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import docking.platform.status_notifier.service as service_mod
from docking.applets.worker import BackgroundWorker
from docking.platform.status_notifier.backend import (
    StatusTrayState,
    unavailable_state,
)
from docking.platform.status_notifier.service import (
    POLL_INTERVAL_S,
    StatusNotifierService,
)


def _available_state() -> StatusTrayState:
    return StatusTrayState(
        available=True,
        watcher_mode="watcher",
        items=(),
        error="",
    )


class _Backend:
    def __init__(self, state: StatusTrayState | None = None) -> None:
        self.state = state if state is not None else _available_state()
        self.get_state_calls = 0
        self.close_calls = 0
        self.activated: list[str] = []
        self.context_menus: list[str] = []

    def get_state(self) -> StatusTrayState:
        self.get_state_calls += 1
        return self.state

    def activate(self, identifier: str) -> bool:
        self.activated.append(identifier)
        return True

    def context_menu(self, identifier: str) -> bool:
        self.context_menus.append(identifier)
        return True

    def menu_client(self, identifier: str):
        return f"client:{identifier}"

    def close(self) -> None:
        self.close_calls += 1


class _RaisingBackend(_Backend):
    def get_state(self) -> StatusTrayState:
        self.get_state_calls += 1
        raise RuntimeError("D-Bus unavailable")


class _ImmediateWorker:
    def run_guarded(self, *, fn, on_result=None, on_error=None, **_kwargs) -> bool:
        try:
            result = fn()
        except Exception as exc:
            if on_error is not None:
                on_error(exc)
            return True
        if on_result is not None:
            on_result(result)
        return True


class _DeferredWorker:
    def __init__(self) -> None:
        self._fn = None
        self._on_result = None

    def run_guarded(self, *, key, fn, on_result=None, **_kwargs) -> bool:
        self._fn = fn
        self._on_result = on_result
        return True

    def deliver(self) -> None:
        assert self._fn is not None and self._on_result is not None
        self._on_result(self._fn())


class _IdleQueue:
    """Collect GLib.idle_add callbacks instead of running the main loop."""

    def __init__(self) -> None:
        self._callbacks: list[tuple[object, tuple]] = []

    def __call__(self, callback, *args) -> int:
        self._callbacks.append((callback, args))
        return 1

    def flush(self) -> None:
        while self._callbacks:
            callback, args = self._callbacks.pop(0)
            callback(*args)  # type: ignore[operator]


class _Threads:
    """Collect worker threads instead of starting them."""

    def __init__(self) -> None:
        self.targets: list[object] = []

    def __call__(self, *, target, daemon) -> SimpleNamespace:
        self.targets.append(target)
        return SimpleNamespace(start=lambda: None)

    def run_all(self) -> None:
        while self.targets:
            target = self.targets.pop(0)
            target()  # type: ignore[operator]


@pytest.fixture
def timers(monkeypatch) -> SimpleNamespace:
    """Keep service timers out of the real main context."""
    added: list[tuple[int, object]] = []
    removed: list[int] = []
    monkeypatch.setattr(
        service_mod.GLib,
        "timeout_add_seconds",
        lambda seconds, callback: added.append((seconds, callback)) or 77,
    )
    monkeypatch.setattr(
        service_mod.GLib,
        "source_remove",
        lambda timer_id: removed.append(timer_id),
    )
    return SimpleNamespace(added=added, removed=removed)


def _service(*, backend=None, worker=None) -> StatusNotifierService:
    return StatusNotifierService(
        backend=_Backend() if backend is None else backend,
        worker=_ImmediateWorker() if worker is None else worker,
    )


class TestPolling:
    def test_start_polls_once_and_schedules_the_interval(self, timers):
        backend = _Backend()
        service = _service(backend=backend)

        service.start()

        assert backend.get_state_calls == 1
        assert timers.added == [(POLL_INTERVAL_S, service._tick)]

    def test_start_is_idempotent(self, timers):
        backend = _Backend()
        service = _service(backend=backend)

        service.start()
        service.start()

        assert backend.get_state_calls == 1
        assert len(timers.added) == 1

    def test_tick_polls_again_and_keeps_running(self, timers):
        backend = _Backend()
        service = _service(backend=backend)
        service.start()

        assert service._tick() is True
        assert backend.get_state_calls == 2

    def test_refresh_before_start_is_ignored(self):
        backend = _Backend()
        service = _service(backend=backend)

        service.refresh()

        assert backend.get_state_calls == 0

    def test_overlapping_refresh_requests_run_one_poll(self, timers):
        backend = _Backend()
        idle = _IdleQueue()
        threads = _Threads()
        worker = BackgroundWorker(idle_add=idle, thread_factory=threads)
        service = StatusNotifierService(backend=backend, worker=worker)

        service.start()
        service.refresh()

        assert len(threads.targets) == 1
        assert backend.get_state_calls == 0

        threads.run_all()
        idle.flush()

        assert backend.get_state_calls == 1
        assert service.state == _available_state()

        # The result released the guard, so the next request polls again.
        service.refresh()

        assert len(threads.targets) == 1

    def test_poll_error_keeps_the_previous_state(self, timers):
        backend = _RaisingBackend()
        service = _service(backend=backend)
        listeners: list[StatusTrayState] = []
        service.add_listener(listeners.append)

        service.start()

        assert listeners == [unavailable_state()]
        assert service.state == unavailable_state()

    def test_late_result_after_stop_is_dropped(self, timers):
        worker = _DeferredWorker()
        service = _service(worker=worker)
        received: list[StatusTrayState] = []
        service.add_listener(received.append)
        service.start()
        received.clear()

        service.stop()
        worker.deliver()

        assert received == []


class TestListeners:
    def test_add_listener_delivers_the_current_state(self):
        service = _service()
        received: list[StatusTrayState] = []

        service.add_listener(received.append)

        assert received == [unavailable_state()]

    def test_every_listener_receives_each_poll(self, timers):
        service = _service()
        first: list[StatusTrayState] = []
        second: list[StatusTrayState] = []
        service.add_listener(first.append)
        service.add_listener(second.append)

        service.start()

        assert first == [unavailable_state(), _available_state()]
        assert second == first

    def test_removed_listener_stops_receiving(self, timers):
        service = _service()
        received: list[StatusTrayState] = []
        service.add_listener(received.append)
        service.remove_listener(received.append)
        received.clear()

        service.start()

        assert received == []

    def test_add_listener_is_idempotent(self):
        service = _service()
        received: list[StatusTrayState] = []

        service.add_listener(received.append)
        service.add_listener(received.append)
        received.clear()
        service._running = True
        service._on_state_result(_available_state())

        assert received == [_available_state()]


class TestLifecycle:
    def test_stop_cancels_the_timer_and_closes_the_backend_once(self, timers):
        backend = _Backend()
        service = _service(backend=backend)
        service.start()

        service.stop()
        service.stop()

        assert timers.removed == [77]
        assert backend.close_calls == 1

    def test_stopped_service_does_not_poll(self, timers):
        backend = _Backend()
        service = _service(backend=backend)
        service.start()
        service.stop()

        service.refresh()

        assert backend.get_state_calls == 1

    def test_restart_polls_again(self, timers):
        backend = _Backend()
        service = _service(backend=backend)
        service.start()
        service.stop()

        service.start()

        assert backend.get_state_calls == 2


class TestTrayActions:
    def test_delegates_to_the_backend(self):
        backend = _Backend()
        service = _service(backend=backend)

        assert service.activate(":1.42/Item") is True
        assert service.context_menu(":1.42/Item") is True
        assert service.menu_client(":1.42/Item") == "client::1.42/Item"
        assert backend.activated == [":1.42/Item"]
        assert backend.context_menus == [":1.42/Item"]
