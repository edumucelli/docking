"""Tests for the isolated raw-Xlib shortcut fallback."""

from __future__ import annotations

import os

import pytest

import docking.search.services.x11_shortcuts as x11_mod
from docking.search.services.x11_shortcuts import (
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


def test_parse_xdg_shortcut_rejects_shortcuts_without_a_usable_key() -> None:
    for shortcut in ("", "   ", "+", "CTRL++ALT", "SHIFT"):
        with pytest.raises(ValueError, match=r"must contain a key|non-modifier key"):
            parse_xdg_shortcut(shortcut)


def test_parse_xdg_shortcut_rejects_unknown_modifiers() -> None:
    with pytest.raises(ValueError, match="unknown shortcut modifier: HYPER"):
        parse_xdg_shortcut("HYPER+space")


def test_parse_xdg_shortcut_rejects_keys_that_x11_cannot_grab() -> None:
    for shortcut in ("CTRL+sp ace", "CTRL+sp/ace", "CTRL+!!"):
        with pytest.raises(ValueError, match="invalid shortcut key"):
            parse_xdg_shortcut(shortcut)


def test_parse_xdg_shortcut_is_case_insensitive_and_ignores_padding() -> None:
    modifiers, key = parse_xdg_shortcut("  ctrl + alt + F5  ")

    assert modifiers == CONTROL_MASK | MOD1_MASK
    assert key == "F5"

    # A bare key with no modifier is still a valid grab.
    assert parse_xdg_shortcut("F5") == (0, "F5")
    assert parse_xdg_shortcut("Super")[1] == "Super"


def test_session_detection_falls_back_to_display_variables() -> None:
    # No XDG_SESSION_TYPE: DISPLAY alone means X11, but a Wayland socket wins.
    assert is_x11_session({"DISPLAY": ":0"})
    assert not is_x11_session({"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"})
    assert not is_x11_session({})
    assert not is_x11_session({"WAYLAND_DISPLAY": "wayland-0"})
    # An explicit session type overrides the ambient variables.
    assert not is_x11_session({"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"})


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


def test_service_dispatches_only_current_worker_generation(monkeypatch) -> None:
    workers = [_FakeWorker(), _FakeWorker()]
    activated = []
    queued = []
    monkeypatch.setattr(
        x11_mod,
        "_ProcessShortcutWorker",
        lambda **_kwargs: workers.pop(0),
    )
    service = X11GlobalShortcutService(
        shortcut="CTRL+ALT+space",
        on_activated=activated.append,
        schedule_idle=lambda callback, *args: queued.append((callback, args)) or 1,
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


def test_service_reports_worker_conflict_and_clears_error_on_stop(monkeypatch) -> None:
    worker = _FakeWorker(starts=False, error="shortcut is already in use")
    monkeypatch.setattr(
        x11_mod,
        "_ProcessShortcutWorker",
        lambda **_kwargs: worker,
    )
    service = X11GlobalShortcutService(
        shortcut="CTRL+ALT+space",
        on_activated=lambda _timestamp: None,
        schedule_idle=lambda _callback, *_args: 1,
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
