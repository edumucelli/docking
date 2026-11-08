"""Tests for the shared StatusNotifier poll service."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import docking.platform.tray.service as service_mod
from docking.applets.worker import BackgroundWorker, SerialWorker
from docking.platform.backends.base import Rect
from docking.platform.tray.backend import TrayBackend
from docking.platform.tray.service import (
    POLL_INTERVAL_S,
)
from docking.platform.tray.service import (
    TrayService as StatusNotifierService,
)
from docking.platform.tray.state import TrayFocusRequest, unavailable_state
from docking.platform.tray.state import TrayState as StatusTrayState
from tests.platform_factories import make_tray_service


def test_presentation_geometry_and_action_anchor_are_forwarded_then_rejected_on_stop():
    backend = Mock()
    service = _service(backend=backend)
    rect = Rect(350, 420, 24, 24)
    service.set_geometry({"icon": rect})
    backend.set_geometry.assert_called_once_with({"icon": rect})
    assert service.context_menu("icon", anchor=rect)
    backend.context_menu.assert_called_once_with("icon", anchor=rect, keyboard=False)
    service.stop()
    backend.reset_mock()
    service.set_geometry({"icon": rect})
    assert not service.activate("icon", anchor=rect)
    assert not backend.mock_calls


def test_pointer_and_keyboard_origin_forward_exact_physical_geometry():
    backend = Mock()
    service = _service(backend=backend)
    rect = Rect(-120, 420, 24, 24)
    assert service.pointer("icon", point=(-115, 425), anchor=rect)
    backend.pointer.assert_called_once_with("icon", point=(-115, 425), anchor=rect)
    assert service.activate("icon", anchor=rect, keyboard=True)
    backend.activate.assert_called_once_with("icon", anchor=rect, keyboard=True)
    assert service.context_menu("icon", anchor=rect, keyboard=True)
    backend.context_menu.assert_called_once_with("icon", anchor=rect, keyboard=True)
    service.stop()
    backend.reset_mock()
    assert not service.pointer(None)
    assert not backend.mock_calls


@pytest.mark.parametrize(
    "command", ["pointer", "activate", "context_menu", "double_click"]
)
def test_queued_commands_reject_a_restarted_service_generation(timers, command):
    backend = Mock()
    service = _service(backend=backend)
    service.start()
    generation = service.generation
    service.stop()
    service.start()
    backend.reset_mock()

    assert not getattr(service, command)("icon", generation=generation)
    assert not backend.mock_calls


@pytest.mark.parametrize(
    "command", ["pointer", "activate", "context_menu", "double_click"]
)
def test_command_backend_is_pinned_before_the_final_generation_check(
    timers, monkeypatch, command
):
    first, second = Mock(), Mock()
    backends = iter((first, second))
    service = make_tray_service(
        backend_factory=lambda: next(backends), worker=_ImmediateWorker()
    )
    service.start()
    generation = service.generation
    check = service._command_current

    def restart_after_check(expected):
        accepted = check(expected)
        service.stop()
        service.start()
        return accepted

    monkeypatch.setattr(service, "_command_current", restart_after_check)
    getattr(service, command)("icon", generation=generation)

    getattr(first, command).assert_called_once()
    getattr(second, command).assert_not_called()


def _available_state() -> StatusTrayState:
    return StatusTrayState(
        available=True,
        mode="watcher",
        items=(),
        error="",
    )


class _Backend(TrayBackend):
    def __init__(self, state: StatusTrayState | None = None) -> None:
        self.state = state if state is not None else _available_state()
        self.get_state_calls = 0
        self.close_calls = 0
        self.activated: list[str] = []
        self.context_menus: list[str] = []
        self.focus_handler = None

    def set_focus_handler(self, handler) -> None:
        self.focus_handler = handler

    def set_geometry(self, bounds) -> None:
        self.bounds = dict(bounds)

    def pointer(self, identifier, *, point=None, anchor=None) -> bool:
        return False

    def get_state(self) -> StatusTrayState:
        self.get_state_calls += 1
        return self.state

    def activate(self, identifier: str, *, anchor=None, keyboard=False) -> bool:
        self.activated.append(identifier)
        return True

    def context_menu(self, identifier: str, *, anchor=None, keyboard=False) -> bool:
        self.context_menus.append(identifier)
        return True

    def double_click(self, identifier: str, *, anchor=None) -> bool:
        self.activated.append(identifier)
        return True

    def menu_client(self, identifier: str):
        return f"client:{identifier}"

    def close(self) -> None:
        self.close_calls += 1

    def initial_state(self):
        return unavailable_state()


class _RaisingBackend(_Backend):
    def get_state(self) -> StatusTrayState:
        self.get_state_calls += 1
        raise RuntimeError("D-Bus unavailable")

    def initial_state(self):
        return unavailable_state()


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
    return make_tray_service(
        backend=_Backend() if backend is None else backend,
        worker=_ImmediateWorker() if worker is None else worker,
    )


class TestPolling:
    def test_start_polls_once_and_schedules_the_interval(self, timers):
        backend = _Backend()
        service = _service(backend=backend)

        service.start()

        assert backend.get_state_calls == 1
        assert len(timers.added) == 1
        assert timers.added[0][0] == POLL_INTERVAL_S and callable(timers.added[0][1])

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
        service = make_tray_service(backend=backend, worker=worker)

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


class TestFocusRequests:
    def test_native_callback_waits_for_main_loop_then_revalidates_at_claim(
        self, timers, monkeypatch
    ):
        idle, backend = _IdleQueue(), _Backend()
        monkeypatch.setattr(service_mod.GLib, "idle_add", idle)
        service = _service(backend=backend)
        received = []
        service.add_focus_listener(received.append)
        service.start()
        claim = Mock(return_value=True)

        backend.focus_handler(TrayFocusRequest("icon", claim, lambda: True))

        assert not received
        claim.assert_not_called()
        idle.flush()
        assert len(received) == 1 and received[0].identifier == "icon"
        claim.assert_not_called()
        assert received[0].validate() and received[0].validate()
        assert received[0].claim()
        claim.assert_called_once_with()

    @pytest.mark.parametrize("deliver_before_remove", [False, True])
    def test_removing_listener_invalidates_queued_and_detached_claims(
        self, timers, monkeypatch, deliver_before_remove
    ):
        idle, backend = _IdleQueue(), _Backend()
        monkeypatch.setattr(service_mod.GLib, "idle_add", idle)
        service = _service(backend=backend)
        received = []
        service.add_focus_listener(received.append)
        service.start()
        claim = Mock(return_value=True)
        old_handler = backend.focus_handler
        old_handler(TrayFocusRequest("icon", claim, lambda: True))
        if deliver_before_remove:
            idle.flush()

        service.remove_focus_listener(received.append)
        assert backend.focus_handler is None
        service.add_focus_listener(received.append)
        old_handler(TrayFocusRequest("icon", claim, lambda: True))
        idle.flush()

        if deliver_before_remove:
            assert len(received) == 1
            assert not received[0].validate() and not received[0].claim()
        else:
            assert not received
        claim.assert_not_called()

    def test_stop_and_restart_reject_old_host_callback(self, timers, monkeypatch):
        idle, backend = _IdleQueue(), _Backend()
        monkeypatch.setattr(service_mod.GLib, "idle_add", idle)
        service = _service(backend=backend)
        received = []
        service.add_focus_listener(received.append)
        service.start()
        old_handler = backend.focus_handler
        claim = Mock(return_value=True)
        old_handler(TrayFocusRequest("icon", claim, lambda: True))
        service.stop()
        assert backend.focus_handler is None
        service.start()
        service.add_focus_listener(received.append)
        old_handler(TrayFocusRequest("icon", claim, lambda: True))
        idle.flush()

        assert not received
        claim.assert_not_called()


class TestLifecycle:
    def test_restart_replaces_native_owner_and_discards_old_callbacks(self, timers):
        backends = []
        idle = _IdleQueue()

        class OwnedBackend(_Backend):
            def __init__(self):
                super().__init__(
                    StatusTrayState(True, f"owner-{len(backends)}", (), "")
                )
                self.closed = threading.Event()
                self.result_queued = threading.Event()
                self.owner_threads = []

            def get_state(self):
                self.owner_threads.append(threading.get_ident())
                return super().get_state()

            def close(self):
                self.owner_threads.append(threading.get_ident())
                super().close()
                self.closed.set()

        def backend_factory():
            backend = OwnedBackend()
            backends.append(backend)
            return backend

        def worker_factory(**kwargs):
            backend = backends[-1]

            def enqueue(callback, *args):
                idle(callback, *args)
                backend.result_queued.set()

            return SerialWorker(idle_add=enqueue, **kwargs)

        service = StatusNotifierService(
            backend_factory=backend_factory, worker_factory=worker_factory
        )
        received = []
        try:
            service.start()
            first = backends[0]
            assert first.result_queued.wait(2)
            service.stop()
            assert first.closed.wait(2)

            service.add_listener(received.append)
            received.clear()
            service.start()
            second = backends[1]
            assert second.result_queued.wait(2)
            idle.flush()

            assert received == [second.state]
            assert service.state == second.state
        finally:
            service.stop()
            service.stop()
            for backend in backends:
                assert backend.closed.wait(2)

        assert len(backends) == 2
        for backend in backends:
            assert backend.get_state_calls == 1
            assert backend.close_calls == 1
            assert len(set(backend.owner_threads)) == 1
            assert backend.owner_threads[0] != threading.get_ident()

    def test_stop_before_start_closes_the_created_backend(self, timers):
        closed = threading.Event()
        backend = _Backend()

        def close():
            backend.close()
            closed.set()

        service = StatusNotifierService(
            backend_factory=lambda: backend,
            worker_factory=lambda **_kwargs: SerialWorker(on_close=close),
        )
        service.stop()
        service.stop()

        assert closed.wait(2)
        assert backend.get_state_calls == 0
        assert backend.close_calls == 1
        assert timers.added == []

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


def test_stale_poll_result_and_timer_are_rejected_after_restart(timers):
    worker = _DeferredWorker()
    service = _service(worker=worker)
    service.start()
    old_result = worker._on_result
    old_timer = timers.added[0][1]
    service.stop()
    service.start()
    received = []
    service.add_listener(received.append)
    received.clear()
    old_result(_available_state())
    assert received == [] and service.state == unavailable_state()
    assert old_timer() is False
    worker.deliver()
    assert received == [_available_state()]


def test_owned_backend_is_recreated_after_stop(monkeypatch, timers):
    from dataclasses import replace

    first = _Backend(_available_state())
    second = _Backend(replace(_available_state(), mode="host"))
    available = iter((first, second))
    service = make_tray_service(
        backend_factory=lambda: next(available), worker=_ImmediateWorker()
    )
    service.start()
    service.stop()
    service.start()
    assert service.state == second.state
    assert (
        first.close_calls == 1 and first.get_state_calls == second.get_state_calls == 1
    )
    service.stop()
    assert second.close_calls == 1


def test_stop_before_start_releases_backend_and_rejects_actions():
    backend = _Backend()
    service = _service(backend=backend)
    service.stop()
    service.stop()
    assert backend.close_calls == 1
    assert not service.activate("old") and not service.context_menu("old")
    assert service.menu_client("old") is None
    assert not backend.activated and not backend.context_menus
