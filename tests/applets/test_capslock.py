"""Tests for Caps Lock applet."""

from __future__ import annotations

from types import SimpleNamespace

import docking.applets.capslock.applet as capslock_applet_mod
import docking.applets.capslock.state as capslock_state_mod
from docking.applets.capslock.applet import CapslockApplet
from docking.applets.capslock.render import render_icon
from docking.applets.capslock.state import (
    LockKeyState,
    menu_label,
    parse_xset_query,
    query_lock_state,
    tooltip_text,
)
from docking.core.config import Config

XSET_OUTPUT = """Keyboard Control:
  auto repeat:  on    key click percent:  0    LED mask:  00000002
  XKB indicators:
    00: Caps Lock:   on     01: Num Lock:    off    02: Scroll Lock: off
"""


class TestState:
    def test_parse_xset_query(self):
        state = parse_xset_query(XSET_OUTPUT)

        assert state == LockKeyState(available=True, caps_lock=True, num_lock=False)

    def test_parse_xset_query_unavailable_when_missing_indicators(self):
        state = parse_xset_query("Keyboard Control:\n")

        assert state == LockKeyState(available=False)

    def test_query_lock_state_without_xset(self, monkeypatch):
        monkeypatch.setattr(capslock_state_mod.shutil, "which", lambda _cmd: None)

        assert query_lock_state() == LockKeyState(available=False)

    def test_query_lock_state_from_xset(self, monkeypatch):
        monkeypatch.setattr(capslock_state_mod.shutil, "which", lambda _cmd: "/bin/x")
        monkeypatch.setattr(
            capslock_state_mod.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0,
                stdout=XSET_OUTPUT,
            ),
        )

        assert query_lock_state() == LockKeyState(
            available=True,
            caps_lock=True,
            num_lock=False,
        )

    def test_query_lock_state_handles_failures(self, monkeypatch):
        monkeypatch.setattr(capslock_state_mod.shutil, "which", lambda _cmd: "/bin/x")
        monkeypatch.setattr(
            capslock_state_mod.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""),
        )
        assert query_lock_state() == LockKeyState(available=False)

        monkeypatch.setattr(
            capslock_state_mod.subprocess,
            "run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("boom")),
        )
        assert query_lock_state() == LockKeyState(available=False)

    def test_state_labels_cover_all_combinations(self):
        assert capslock_state_mod.state_label(LockKeyState(False)) == "??"
        assert capslock_state_mod.state_label(LockKeyState(True, True, True)) == "CN"
        assert capslock_state_mod.state_label(LockKeyState(True, True, False)) == "CAP"
        assert capslock_state_mod.state_label(LockKeyState(True, False, True)) == "NUM"
        assert capslock_state_mod.state_label(LockKeyState(True, False, False)) == "--"

    def test_tooltip_text(self):
        text = tooltip_text(
            LockKeyState(available=True, caps_lock=True, num_lock=False)
        )

        assert "Caps Lock: On" in text
        assert "Num Lock: Off" in text

    def test_menu_label(self):
        assert menu_label("Caps Lock", True) == "Caps Lock: On"
        assert tooltip_text(LockKeyState(False)) == "Keyboard lock state unavailable"


class TestRender:
    def test_render_icon(self):
        assert (
            render_icon(
                size=48,
                state=LockKeyState(available=True, caps_lock=True, num_lock=False),
            )
            is not None
        )
        assert render_icon(size=48, state=LockKeyState(available=False)) is not None


class TestApplet:
    def test_applet_presents_state(self, monkeypatch):
        state = LockKeyState(available=True, caps_lock=True, num_lock=True)
        monkeypatch.setattr(capslock_applet_mod, "query_lock_state", lambda: state)

        applet = CapslockApplet(icon_size=48, config=Config())

        assert applet.item.desktop_id == "applet://capslock"
        assert "Caps Lock: On" in applet.item.name
        assert "Num Lock: On" in applet.item.name
        assert applet.item.icon is not None

    def test_menu_contains_refresh(self, monkeypatch):
        state = LockKeyState(available=True, caps_lock=False, num_lock=True)
        monkeypatch.setattr(capslock_applet_mod, "query_lock_state", lambda: state)
        applet = CapslockApplet(icon_size=48, config=Config())

        labels = [item.get_label() for item in applet.get_menu_items()]

        assert "Caps Lock: Off" in labels
        assert "Num Lock: On" in labels
        assert "Refresh Now" in labels

    def test_menu_unavailable_and_click_refresh(self, monkeypatch):
        states = iter(
            [
                LockKeyState(False),
                LockKeyState(True, True, False),
            ]
        )
        monkeypatch.setattr(
            capslock_applet_mod,
            "query_lock_state",
            lambda: next(states),
        )
        applet = CapslockApplet(icon_size=48, config=Config())
        labels = [item.get_label() for item in applet.get_menu_items()]
        assert "Keyboard lock state unavailable" in labels

        applet.present = lambda: None
        applet.on_clicked()
        assert applet._state == LockKeyState(True, True, False)

    def test_start_stop_manage_timer(self, monkeypatch):
        state = LockKeyState(available=True)
        monkeypatch.setattr(capslock_applet_mod, "query_lock_state", lambda: state)
        monkeypatch.setattr(
            capslock_applet_mod.GLib,
            "timeout_add_seconds",
            lambda interval, cb: 42,
        )
        removed: list[int] = []
        monkeypatch.setattr(
            capslock_applet_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )
        applet = CapslockApplet(icon_size=48, config=Config())

        applet.start(lambda: None)
        applet.stop()

        assert applet._timer_id == 0
        assert removed == [42]

    def test_tick_updates_when_state_changes(self, monkeypatch):
        states = iter(
            [
                LockKeyState(available=True, caps_lock=False, num_lock=False),
                LockKeyState(available=True, caps_lock=True, num_lock=False),
            ]
        )
        monkeypatch.setattr(
            capslock_applet_mod, "query_lock_state", lambda: next(states)
        )
        applet = CapslockApplet(icon_size=48, config=Config())
        calls = 0

        def present():
            nonlocal calls
            calls += 1

        applet.present = present

        assert applet._tick() is True
        assert calls == 1

    def test_tick_does_not_present_when_state_is_same(self, monkeypatch):
        state = LockKeyState(available=True, caps_lock=False, num_lock=False)
        monkeypatch.setattr(capslock_applet_mod, "query_lock_state", lambda: state)
        applet = CapslockApplet(icon_size=48, config=Config())
        applet.present = lambda: (_ for _ in ()).throw(AssertionError("no present"))

        assert applet._tick() is True
