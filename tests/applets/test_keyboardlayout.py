"""Tests for keyboard layout applet."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

try:
    import gi  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

import docking.applets.keyboardlayout.state as kbl_state
from docking.applets.keyboardlayout.state import (
    Fcitx5Backend,
    IBusBackend,
    XkbBackend,
    _fcitx5_layout_code,
    _ibus_layout_code,
    cycle_layout,
    detect_backend,
    layout_display_name,
    layout_label,
    tooltip_text,
)

SETXKBMAP_MULTI = """\
rules:      evdev
model:      pc105
layout:     us,es,fr
variant:    ,dvorak,"""

IBUS_ENGINES_DCONF = "['xkb:us::eng', 'xkb:br::por']"

FCITX5_PROFILE = """\
[Groups/0]
Name=Default
Default Layout=us
DefaultIM=keyboard-us

[Groups/0/Items/0]
Name=keyboard-us
Layout=

[Groups/0/Items/1]
Name=keyboard-br
Layout=

[Groups/1]
Name=Other
"""


# ---------------------------------------------------------------------------
# Layout code extraction
# ---------------------------------------------------------------------------


class TestLayoutCodeExtraction:
    def test_ibus_xkb_engine(self):
        assert _ibus_layout_code("xkb:us::eng") == "us"
        assert _ibus_layout_code("xkb:br::por") == "br"
        assert _ibus_layout_code("xkb:de:neo:deu") == "de"

    def test_ibus_non_xkb_returns_raw(self):
        assert _ibus_layout_code("anthy") == "anthy"

    def test_fcitx5_keyboard_im(self):
        assert _fcitx5_layout_code("keyboard-us") == "us"
        assert _fcitx5_layout_code("keyboard-br") == "br"

    def test_fcitx5_non_keyboard_returns_raw(self):
        assert _fcitx5_layout_code("pinyin") == "pinyin"


# ---------------------------------------------------------------------------
# IBusBackend
# ---------------------------------------------------------------------------


class TestIBusBackend:
    def test_query_returns_layouts(self, monkeypatch):
        def mock_run(cmd):
            if cmd == ["ibus", "engine"]:
                return "xkb:us::eng"
            if cmd[:2] == ["dconf", "read"]:
                return IBUS_ENGINES_DCONF
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)
        backend = IBusBackend()
        state = backend.query()
        assert state.active == "us"
        assert state.available == ["us", "br"]

    def test_is_available_true(self, monkeypatch):
        monkeypatch.setattr(
            kbl_state,
            "_run",
            lambda cmd: "xkb:us::eng" if cmd == ["ibus", "engine"] else None,
        )
        assert IBusBackend().is_available()

    def test_is_available_false(self, monkeypatch):
        monkeypatch.setattr(kbl_state, "_run", lambda cmd: None)
        assert not IBusBackend().is_available()

    def test_switch_calls_ibus_engine(self, monkeypatch):
        commands = []

        def mock_run(cmd):
            commands.append(cmd)
            if cmd[:2] == ["dconf", "read"]:
                return IBUS_ENGINES_DCONF
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)
        IBusBackend().switch(layout_code="br")
        assert ["ibus", "engine", "xkb:br::por"] in commands

    def test_active_engine_reflected(self, monkeypatch):
        def mock_run(cmd):
            if cmd == ["ibus", "engine"]:
                return "xkb:br::por"
            if cmd[:2] == ["dconf", "read"]:
                return IBUS_ENGINES_DCONF
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)
        state = IBusBackend().query()
        assert state.active == "br"


# ---------------------------------------------------------------------------
# Fcitx5Backend
# ---------------------------------------------------------------------------


class TestFcitx5Backend:
    def test_query_returns_layouts(self, monkeypatch, tmp_path):
        profile = tmp_path / ".config" / "fcitx5" / "profile"
        profile.parent.mkdir(parents=True)
        profile.write_text(FCITX5_PROFILE)

        def mock_run(cmd):
            if cmd == ["fcitx5-remote", "-n"]:
                return "keyboard-us"
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)
        monkeypatch.setattr(kbl_state.Path, "home", lambda: tmp_path)

        backend = Fcitx5Backend()
        state = backend.query()
        assert state.active == "us"
        assert state.available == ["us", "br"]

    def test_is_available_true(self, monkeypatch):
        monkeypatch.setattr(
            kbl_state,
            "_run",
            lambda cmd: "keyboard-us" if cmd == ["fcitx5-remote", "-n"] else None,
        )
        assert Fcitx5Backend().is_available()

    def test_is_available_false(self, monkeypatch):
        monkeypatch.setattr(kbl_state, "_run", lambda cmd: None)
        assert not Fcitx5Backend().is_available()

    def test_switch_calls_fcitx5_remote(self, monkeypatch, tmp_path):
        profile = tmp_path / ".config" / "fcitx5" / "profile"
        profile.parent.mkdir(parents=True)
        profile.write_text(FCITX5_PROFILE)
        monkeypatch.setattr(kbl_state.Path, "home", lambda: tmp_path)

        commands = []
        monkeypatch.setattr(
            kbl_state,
            "_run",
            lambda cmd: commands.append(cmd) or None,
        )
        Fcitx5Backend().switch(layout_code="br")
        assert ["fcitx5-remote", "-s", "keyboard-br"] in commands

    def test_empty_profile(self, monkeypatch, tmp_path):
        monkeypatch.setattr(kbl_state.Path, "home", lambda: tmp_path)

        def mock_run(cmd):
            if cmd == ["fcitx5-remote", "-n"]:
                return "keyboard-us"
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)
        state = Fcitx5Backend().query()
        assert state.active == "us"
        assert state.available == ["us"]


# ---------------------------------------------------------------------------
# XkbBackend
# ---------------------------------------------------------------------------


class TestXkbBackend:
    def test_query_returns_layouts(self, monkeypatch):
        monkeypatch.setattr(
            kbl_state,
            "_run",
            lambda cmd: SETXKBMAP_MULTI if cmd == ["setxkbmap", "-query"] else None,
        )
        state = XkbBackend().query()
        assert state.active == "us"
        assert state.available == ["us", "es", "fr"]

    def test_switch_calls_setxkbmap(self, monkeypatch):
        commands = []
        monkeypatch.setattr(
            kbl_state,
            "_run",
            lambda cmd: commands.append(cmd) or None,
        )
        XkbBackend().switch(layout_code="es")
        assert ["setxkbmap", "-layout", "es"] in commands


# ---------------------------------------------------------------------------
# detect_backend
# ---------------------------------------------------------------------------


class TestDetectBackend:
    def test_prefers_ibus(self, monkeypatch):
        monkeypatch.setattr(
            kbl_state,
            "_run",
            lambda cmd: "xkb:us::eng" if cmd == ["ibus", "engine"] else None,
        )
        backend = detect_backend()
        assert isinstance(backend, IBusBackend)

    def test_falls_back_to_fcitx5(self, monkeypatch):
        def mock_run(cmd):
            if cmd == ["ibus", "engine"]:
                return None
            if cmd == ["fcitx5-remote", "-n"]:
                return "keyboard-us"
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)
        backend = detect_backend()
        assert isinstance(backend, Fcitx5Backend)

    def test_falls_back_to_xkb(self, monkeypatch):
        call_count = {"ibus": 0, "fcitx5": 0}

        def mock_run(cmd):
            if cmd[0] == "ibus":
                call_count["ibus"] += 1
                return None
            if cmd[0] == "fcitx5-remote":
                call_count["fcitx5"] += 1
                return None
            if cmd == ["setxkbmap", "-query"]:
                return "layout:     us"
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)
        backend = detect_backend()
        assert isinstance(backend, XkbBackend)


# ---------------------------------------------------------------------------
# cycle_layout / labels / tooltip
# ---------------------------------------------------------------------------


class TestCycleLayout:
    def test_cycles_to_next(self):
        assert cycle_layout(current="us", available=["us", "br"]) == "br"

    def test_wraps_around(self):
        assert cycle_layout(current="br", available=["us", "br"]) == "us"

    def test_unknown_current_returns_first(self):
        assert cycle_layout(current="de", available=["us", "br"]) == "us"

    def test_empty_available(self):
        assert cycle_layout(current="us", available=[]) == "us"


class TestLabels:
    def test_known_label(self):
        assert layout_label(code="us") == "EN"
        assert layout_label(code="br") == "BR"

    def test_unknown_label(self):
        assert layout_label(code="xyz") == "XY"

    def test_display_name(self):
        assert layout_display_name(code="br") == "Portuguese (BR)"

    def test_tooltip(self):
        assert tooltip_text(active="br") == "Portuguese (BR)"


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


class TestRender:
    @pytest.mark.parametrize("label", ["EN", "BR", "??"])
    def test_render_icon_returns_pixbuf(self, label):
        from docking.applets.keyboardlayout.render import render_icon

        assert render_icon(size=48, label=label) is not None

    @pytest.mark.parametrize("size", [32, 48, 64])
    def test_render_at_various_sizes(self, size):
        from docking.applets.keyboardlayout.render import render_icon

        result = render_icon(size=size, label="EN")
        assert result is not None
        assert result.get_width() == size


# ---------------------------------------------------------------------------
# applet lifecycle
# ---------------------------------------------------------------------------


class TestKeyboardLayoutApplet:
    def _mock_ibus(self, monkeypatch):
        def mock_run(cmd):
            if cmd == ["ibus", "engine"]:
                return "xkb:us::eng"
            if cmd[:2] == ["dconf", "read"]:
                return IBUS_ENGINES_DCONF
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)

    def test_creates_with_layout(self, monkeypatch):
        self._mock_ibus(monkeypatch)
        from docking.applets.keyboardlayout.applet import KeyboardLayoutApplet

        applet = KeyboardLayoutApplet(icon_size=48)
        assert applet._active == "us"
        assert applet._available == ["us", "br"]

    def test_tooltip_shows_active_name(self, monkeypatch):
        self._mock_ibus(monkeypatch)
        from docking.applets.keyboardlayout.applet import KeyboardLayoutApplet

        applet = KeyboardLayoutApplet(icon_size=48)
        applet.refresh_tooltip()
        assert applet.item.name == "English (US)"

    def test_on_clicked_cycles(self, monkeypatch):
        commands = []

        def mock_run(cmd):
            commands.append(cmd)
            if cmd == ["ibus", "engine"]:
                return "xkb:us::eng"
            if cmd[:2] == ["dconf", "read"]:
                return IBUS_ENGINES_DCONF
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)
        from docking.applets.keyboardlayout.applet import KeyboardLayoutApplet

        applet = KeyboardLayoutApplet(icon_size=48)
        applet.on_clicked()
        assert applet._active == "br"
        assert ["ibus", "engine", "xkb:br::por"] in commands

    def test_on_scroll(self, monkeypatch):
        self._mock_ibus(monkeypatch)
        from docking.applets.keyboardlayout.applet import KeyboardLayoutApplet

        applet = KeyboardLayoutApplet(icon_size=48)
        applet.on_scroll(direction_up=True)
        assert applet._active == "br"
        applet.on_scroll(direction_up=False)
        assert applet._active == "us"

    def test_menu_items(self, monkeypatch):
        self._mock_ibus(monkeypatch)
        from docking.applets.keyboardlayout.applet import KeyboardLayoutApplet

        applet = KeyboardLayoutApplet(icon_size=48)
        assert len(applet.get_menu_items()) == 2

    def test_no_layout_detected(self, monkeypatch):
        monkeypatch.setattr(kbl_state, "_run", lambda cmd: None)
        from docking.applets.keyboardlayout.applet import KeyboardLayoutApplet

        applet = KeyboardLayoutApplet(icon_size=48)
        applet.refresh_tooltip()
        assert "No keyboard" in applet.item.name
