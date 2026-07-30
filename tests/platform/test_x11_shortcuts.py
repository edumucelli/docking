"""Tests for the isolated raw-Xlib shortcut fallback."""

from __future__ import annotations

import os

import pytest

from docking.platform.x11_shortcuts import (
    CONTROL_MASK,
    MOD1_MASK,
    MOD4_MASK,
    X11GlobalShortcutService,
    is_x11_session,
    parse_xdg_shortcut,
)


def test_parse_xdg_shortcut_and_session_detection() -> None:
    modifiers, key = parse_xdg_shortcut("CTRL+ALT+LOGO+space")

    assert modifiers == CONTROL_MASK | MOD1_MASK | MOD4_MASK
    assert key == "space"
    assert is_x11_session({"XDG_SESSION_TYPE": "x11"})
    assert not is_x11_session({"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"})
    with pytest.raises(ValueError):
        parse_xdg_shortcut("CTRL+ALT")


class _FakeWorker:
    def __init__(self, *, starts: bool = True, error: str | None = None) -> None:
        self.starts = starts
        self._error = error
        self.callback = None
        self.stop_calls = 0

    @property
    def error(self) -> str | None:
        return self._error

    def start(self, on_activated) -> bool:
        self.callback = on_activated
        return self.starts

    def stop(self) -> None:
        self.stop_calls += 1


def test_service_dispatches_only_current_worker_generation() -> None:
    workers = [_FakeWorker(), _FakeWorker()]
    activated = []
    queued = []
    service = X11GlobalShortcutService(
        shortcut="CTRL+ALT+space",
        on_activated=activated.append,
        schedule_idle=lambda callback, *args: queued.append((callback, args)) or 1,
        worker_factory=lambda: workers.pop(0),
    )

    assert service.start()
    first = service._worker
    assert first is not None
    first.callback(1234)
    service.stop()
    assert service.start()
    second = service._worker
    assert second is not None
    second.callback(5678)
    for callback, args in queued:
        callback(*args)

    assert activated == [5678]
    service.stop()


def test_service_reports_worker_conflict_and_clears_error_on_stop() -> None:
    worker = _FakeWorker(starts=False, error="shortcut is already in use")
    service = X11GlobalShortcutService(
        shortcut="CTRL+ALT+space",
        on_activated=lambda _timestamp: None,
        schedule_idle=lambda _callback, *_args: 1,
        worker_factory=lambda: worker,
    )

    assert not service.start()
    assert service.error == "shortcut is already in use"
    service.stop()
    assert service.error is None


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="X11 display unavailable")
def test_real_helper_process_can_grab_and_release_shortcut() -> None:
    service = X11GlobalShortcutService(
        shortcut="CTRL+ALT+SHIFT+F11",
        on_activated=lambda _timestamp: None,
        schedule_idle=lambda _callback, *_args: 1,
    )

    assert service.start(), service.error
    service.stop()
    assert not service.active
