"""Tests for keyboard layout applet."""

from __future__ import annotations

import subprocess
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
    GnomeBackend,
    IBusBackend,
    MateBackend,
    XkbBackend,
    _fcitx5_layout_code,
    _first_available_command,
    _ibus_layout_code,
    _open_command,
    _parse_gsettings_string,
    _parse_gsettings_string_list,
    _parse_input_sources,
    _source_layout_code,
    current_layout_command,
    cycle_layout,
    detect_backend,
    keyboard_settings_command,
    layout_display_name,
    layout_label,
    open_keyboard_settings,
    show_current_layout,
    tooltip_text,
)
from docking.platform.environment import Desktop


def _set_desktop(monkeypatch: pytest.MonkeyPatch, desktop: Desktop) -> None:
    monkeypatch.setattr(kbl_state.environment, "detect_desktop", lambda: desktop)


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
GNOME_SOURCES = "[('xkb', 'us'), ('xkb', 'br')]"
GNOME_MRU_SOURCES = "[('xkb', 'br')]"


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

    def test_query_unavailable_and_fallback_to_active_engine(self, monkeypatch):
        monkeypatch.setattr(kbl_state, "_run", lambda cmd: None)
        assert IBusBackend().query().available == []

        def mock_run(cmd):
            if cmd == ["ibus", "engine"]:
                return "xkb:us::eng"
            if cmd[:2] == ["dconf", "read"]:
                return ""
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)
        assert IBusBackend().query().available == ["us"]

    def test_switch_falls_back_to_synthetic_engine(self, monkeypatch):
        commands = []

        def mock_run(cmd):
            commands.append(cmd)
            if cmd[:2] == ["dconf", "read"]:
                return ""
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)

        IBusBackend().switch(layout_code="de")

        assert ["ibus", "engine", "xkb:de::eng"] in commands


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

    def test_query_unavailable_and_switch_fallback(self, monkeypatch, tmp_path):
        monkeypatch.setattr(kbl_state, "_run", lambda cmd: None)
        monkeypatch.setattr(kbl_state.Path, "home", lambda: tmp_path)
        assert Fcitx5Backend().query().available == []

        commands = []
        monkeypatch.setattr(
            kbl_state,
            "_run",
            lambda cmd: commands.append(cmd) or None,
        )
        Fcitx5Backend().switch(layout_code="de")
        assert ["fcitx5-remote", "-s", "keyboard-de"] in commands

    def test_profile_parser_stops_at_next_group(self, monkeypatch, tmp_path):
        profile = tmp_path / ".config" / "fcitx5" / "profile"
        profile.parent.mkdir(parents=True)
        profile.write_text(FCITX5_PROFILE + "\n[Other]\nName=keyboard-de\n")
        monkeypatch.setattr(kbl_state.Path, "home", lambda: tmp_path)

        assert Fcitx5Backend()._get_ims() == ["keyboard-us", "keyboard-br"]


# ---------------------------------------------------------------------------
# MATE backend
# ---------------------------------------------------------------------------


class TestMateBackend:
    def test_parse_gsettings_string_list(self):
        assert _parse_gsettings_string_list("['gb', 'br', 'us']") == ["gb", "br", "us"]
        assert _parse_gsettings_string_list("@as []") == []
        assert _parse_gsettings_string_list("bad [") == []
        assert _parse_gsettings_string_list("'not-list'") == []
        assert _parse_gsettings_string_list("[1, 'us', '']") == ["us"]

    def test_parse_gsettings_string(self):
        assert _parse_gsettings_string("'pc101'") == "pc101"
        assert _parse_gsettings_string("") == ""
        assert _parse_gsettings_string("pc105") == "pc105"
        assert _parse_gsettings_string("123") == ""

    def test_query_returns_mate_layouts(self, monkeypatch):
        _set_desktop(monkeypatch, Desktop.MATE)

        def mock_run(cmd):
            if cmd == [
                "gsettings",
                "get",
                "org.mate.peripherals-keyboard-xkb.kbd",
                "layouts",
            ]:
                return "['gb', 'br', 'us']"
            if cmd == ["setxkbmap", "-query"]:
                return "layout:     br"
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)

        state = MateBackend().query()
        assert state.active == "br"
        assert state.available == ["gb", "br", "us"]

    def test_switch_preserves_mate_model_and_options(self, monkeypatch):
        _set_desktop(monkeypatch, Desktop.MATE)
        commands = []

        def mock_run(cmd):
            commands.append(cmd)
            if cmd == [
                "gsettings",
                "get",
                "org.mate.peripherals-keyboard-xkb.kbd",
                "layouts",
            ]:
                return "['gb', 'br', 'us']"
            if cmd == [
                "gsettings",
                "get",
                "org.mate.peripherals-keyboard-xkb.kbd",
                "model",
            ]:
                return "'pc101'"
            if cmd == [
                "gsettings",
                "get",
                "org.mate.peripherals-keyboard-xkb.kbd",
                "options",
            ]:
                return "['grp:alt_shift_toggle']"
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)

        MateBackend().switch(layout_code="us")

        assert [
            "setxkbmap",
            "-model",
            "pc101",
            "-layout",
            "us,gb,br",
            "-option",
            "grp:alt_shift_toggle",
        ] in commands

    def test_is_available_requires_mate_session(self, monkeypatch):
        _set_desktop(monkeypatch, Desktop.UNKNOWN)
        monkeypatch.setattr(kbl_state, "_run", lambda cmd: "['gb', 'br', 'us']")

        assert not MateBackend().is_available()

    def test_is_available_true_and_query_falls_back_to_first_layout(self, monkeypatch):
        _set_desktop(monkeypatch, Desktop.MATE)

        def mock_run(cmd):
            if cmd == [
                "gsettings",
                "get",
                "org.mate.peripherals-keyboard-xkb.kbd",
                "layouts",
            ]:
                return "['gb', 'br']"
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)

        assert MateBackend().is_available()
        assert MateBackend().query() == kbl_state.LayoutState("gb", ["gb", "br"])

    def test_switch_unknown_layout_without_model_or_options(self, monkeypatch):
        commands = []

        def mock_run(cmd):
            commands.append(cmd)
            if cmd == [
                "gsettings",
                "get",
                "org.mate.peripherals-keyboard-xkb.kbd",
                "layouts",
            ]:
                return "['gb', 'br']"
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)

        MateBackend().switch(layout_code="de")

        assert ["setxkbmap", "-layout", "de"] in commands


class TestGnomeBackend:
    def test_parse_input_sources(self):
        assert _parse_input_sources(GNOME_SOURCES) == [("xkb", "us"), ("xkb", "br")]
        assert _parse_input_sources("@a(ss) []") == []
        assert _parse_input_sources("bad [") == []
        assert _parse_input_sources("'not-list'") == []
        assert _parse_input_sources("[('bad',), ('xkb', 'us'), (1, 'bad')]") == [
            ("xkb", "us")
        ]
        assert _source_layout_code("br+abnt2") == "br"

    def test_query_returns_gnome_sources(self, monkeypatch):
        _set_desktop(monkeypatch, Desktop.UBUNTU | Desktop.GNOME)
        monkeypatch.setattr(kbl_state.shutil, "which", lambda cmd: "/usr/bin/gdbus")

        def mock_run(cmd):
            if cmd == [
                "gsettings",
                "get",
                "org.gnome.desktop.input-sources",
                "sources",
            ]:
                return GNOME_SOURCES
            if cmd == [
                "gsettings",
                "get",
                "org.gnome.desktop.input-sources",
                "current",
            ]:
                return "uint32 1"
            if cmd == [
                "gsettings",
                "get",
                "org.gnome.desktop.input-sources",
                "mru-sources",
            ]:
                return GNOME_MRU_SOURCES
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)
        state = GnomeBackend().query()
        assert state.active == "br"
        assert state.available == ["us", "br"]

    def test_switch_calls_gdbus_eval(self, monkeypatch):
        _set_desktop(monkeypatch, Desktop.UBUNTU | Desktop.GNOME)
        monkeypatch.setattr(kbl_state.shutil, "which", lambda cmd: "/usr/bin/gdbus")
        commands = []

        def mock_run(cmd):
            commands.append(cmd)
            if cmd == [
                "gsettings",
                "get",
                "org.gnome.desktop.input-sources",
                "sources",
            ]:
                return GNOME_SOURCES
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)
        GnomeBackend().switch(layout_code="br")
        assert [
            "gsettings",
            "set",
            "org.gnome.desktop.input-sources",
            "current",
            "1",
        ] in commands
        assert [
            "/usr/bin/gdbus",
            "call",
            "--session",
            "--dest",
            "org.gnome.Shell",
            "--object-path",
            "/org/gnome/Shell",
            "--method",
            "org.gnome.Shell.Eval",
            "imports.ui.status.keyboard.getInputSourceManager().inputSources[1].activate()",
        ] in commands

    def test_is_available_requires_gnome_and_gdbus(self, monkeypatch):
        _set_desktop(monkeypatch, Desktop.UBUNTU | Desktop.GNOME)
        monkeypatch.setattr(kbl_state.shutil, "which", lambda cmd: None)
        monkeypatch.setattr(
            kbl_state,
            "_run",
            lambda cmd: GNOME_SOURCES if cmd[0] == "gsettings" else None,
        )
        assert GnomeBackend().is_available()

    def test_is_available_false_outside_gnome(self, monkeypatch):
        _set_desktop(monkeypatch, Desktop.UNKNOWN)
        monkeypatch.setattr(kbl_state, "_run", lambda cmd: GNOME_SOURCES)
        assert not GnomeBackend().is_available()

    def test_active_source_falls_back_to_mru_and_first_xkb(self, monkeypatch):
        backend = GnomeBackend()

        def mock_run_mru(cmd):
            if cmd[-1] == "current":
                return "uint32 99"
            if cmd[-1] == "mru-sources":
                return GNOME_MRU_SOURCES
            return GNOME_SOURCES

        monkeypatch.setattr(kbl_state, "_run", mock_run_mru)
        assert (
            backend._active_source_code(sources=[("xkb", "us"), ("xkb", "br")]) == "br"
        )

        def mock_run_first(cmd):
            if cmd[-1] == "current":
                return "none"
            if cmd[-1] == "mru-sources":
                return "[('ibus', 'anthy')]"
            return GNOME_SOURCES

        monkeypatch.setattr(kbl_state, "_run", mock_run_first)
        assert (
            backend._active_source_code(sources=[("ibus", "anthy"), ("xkb", "us")])
            == "us"
        )
        assert backend._current_index() == -1

    def test_query_without_active_uses_available_first(self, monkeypatch):
        monkeypatch.setattr(GnomeBackend, "_sources", lambda self: [("xkb", "us")])
        monkeypatch.setattr(
            GnomeBackend, "_active_source_code", lambda self, sources: ""
        )

        assert GnomeBackend().query() == kbl_state.LayoutState("us", ["us"])

    def test_switch_without_gdbus_and_no_match(self, monkeypatch):
        commands = []
        monkeypatch.setattr(
            GnomeBackend,
            "_sources",
            lambda self: [("ibus", "anthy"), ("xkb", "br")],
        )
        monkeypatch.setattr(kbl_state.shutil, "which", lambda cmd: None)
        monkeypatch.setattr(kbl_state, "_run", lambda cmd: commands.append(cmd) or None)

        GnomeBackend().switch(layout_code="br")
        GnomeBackend().switch(layout_code="de")

        assert [
            "gsettings",
            "set",
            "org.gnome.desktop.input-sources",
            "current",
            "1",
        ] in commands


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

    def test_query_empty_and_missing_layout(self, monkeypatch):
        monkeypatch.setattr(kbl_state, "_run", lambda cmd: None)
        assert XkbBackend().query() == kbl_state.LayoutState("", [])

        monkeypatch.setattr(kbl_state, "_run", lambda cmd: "rules: evdev")
        assert XkbBackend().query() == kbl_state.LayoutState("", [])


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

    def test_prefers_gnome_over_ibus_in_gnome_session(self, monkeypatch):
        _set_desktop(monkeypatch, Desktop.UBUNTU | Desktop.GNOME)

        def mock_run(cmd):
            if cmd == [
                "gsettings",
                "get",
                "org.gnome.desktop.input-sources",
                "sources",
            ]:
                return GNOME_SOURCES
            if cmd == [
                "gsettings",
                "get",
                "org.gnome.desktop.input-sources",
                "current",
            ]:
                return "uint32 0"
            if cmd == ["ibus", "engine"]:
                return "xkb:us::eng"
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)
        monkeypatch.setattr(
            kbl_state.shutil,
            "which",
            lambda cmd: "/usr/bin/gdbus" if cmd == "gdbus" else None,
        )
        backend = detect_backend()
        assert isinstance(backend, GnomeBackend)

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

    def test_prefers_mate_before_xkb(self, monkeypatch):
        _set_desktop(monkeypatch, Desktop.MATE)

        def mock_run(cmd):
            if cmd == ["ibus", "engine"]:
                return None
            if cmd == ["fcitx5-remote", "-n"]:
                return None
            if cmd == [
                "gsettings",
                "get",
                "org.mate.peripherals-keyboard-xkb.kbd",
                "layouts",
            ]:
                return "['gb', 'br', 'us']"
            if cmd == ["setxkbmap", "-query"]:
                return "layout:     br"
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)
        backend = detect_backend()
        assert isinstance(backend, MateBackend)

    def test_prefers_mate_before_ibus_in_mate_session(self, monkeypatch):
        _set_desktop(monkeypatch, Desktop.MATE)

        def mock_run(cmd):
            if cmd == ["ibus", "engine"]:
                return "xkb:us::eng"
            if cmd == [
                "gsettings",
                "get",
                "org.mate.peripherals-keyboard-xkb.kbd",
                "layouts",
            ]:
                return "['gb', 'br', 'us']"
            if cmd == ["setxkbmap", "-query"]:
                return "layout:     gb,br,us"
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)
        backend = detect_backend()
        assert isinstance(backend, MateBackend)

    def test_prefers_gnome_before_xkb(self, monkeypatch):
        _set_desktop(monkeypatch, Desktop.UBUNTU | Desktop.GNOME)

        def mock_run(cmd):
            if cmd == ["ibus", "engine"]:
                return None
            if cmd == ["fcitx5-remote", "-n"]:
                return None
            if cmd == [
                "gsettings",
                "get",
                "org.mate.peripherals-keyboard-xkb.kbd",
                "layouts",
            ]:
                return None
            if cmd == [
                "gsettings",
                "get",
                "org.gnome.desktop.input-sources",
                "sources",
            ]:
                return GNOME_SOURCES
            if cmd == [
                "gsettings",
                "get",
                "org.gnome.desktop.input-sources",
                "mru-sources",
            ]:
                return GNOME_MRU_SOURCES
            if cmd == ["setxkbmap", "-query"]:
                return "layout:     us"
            return None

        monkeypatch.setattr(kbl_state, "_run", mock_run)
        monkeypatch.setattr(
            kbl_state.shutil,
            "which",
            lambda cmd: "/usr/bin/gdbus" if cmd == "gdbus" else None,
        )
        backend = detect_backend()
        assert isinstance(backend, GnomeBackend)


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


class TestCommandHelpers:
    def test_keyboard_settings_command(self, monkeypatch):
        commands = {
            "mate-keyboard-properties": None,
            "gnome-control-center": "/usr/bin/gnome-control-center",
            "ibus-setup": "/usr/bin/ibus-setup",
        }
        monkeypatch.setattr(
            kbl_state.shutil,
            "which",
            lambda cmd: commands.get(cmd),
        )
        assert keyboard_settings_command() == ["gnome-control-center", "keyboard"]

    def test_current_layout_command(self, monkeypatch):
        monkeypatch.setattr(
            kbl_state.shutil,
            "which",
            lambda cmd: (
                "/usr/bin/gkbd-keyboard-display"
                if cmd == "gkbd-keyboard-display"
                else None
            ),
        )
        assert current_layout_command("br") == ["gkbd-keyboard-display", "-l", "br"]
        assert current_layout_command("") is None

        monkeypatch.setattr(
            kbl_state.shutil,
            "which",
            lambda cmd: "/usr/bin/tecla" if cmd == "tecla" else None,
        )
        assert current_layout_command("") == ["tecla"]

    def test_open_commands(self, monkeypatch):
        launched: list[list[str]] = []
        monkeypatch.setattr(
            kbl_state,
            "keyboard_settings_command",
            lambda: ["mate-keyboard-properties"],
        )
        monkeypatch.setattr(
            kbl_state,
            "current_layout_command",
            lambda layout_code: ["gkbd-keyboard-display", "-l", layout_code],
        )
        monkeypatch.setattr(
            kbl_state.subprocess,
            "Popen",
            lambda cmd, start_new_session=True: launched.append(list(cmd)),
        )
        assert open_keyboard_settings() is True
        assert show_current_layout("us") is True
        assert launched == [
            ["mate-keyboard-properties"],
            ["gkbd-keyboard-display", "-l", "us"],
        ]

    def test_first_available_and_open_command_failures(self, monkeypatch):
        monkeypatch.setattr(kbl_state.shutil, "which", lambda cmd: None)
        assert _first_available_command((("missing",),)) is None
        assert _open_command(cmd=None, action="missing") is False

        monkeypatch.setattr(
            kbl_state.subprocess,
            "Popen",
            MagicMock(side_effect=OSError("boom")),
        )
        assert _open_command(cmd=["missing"], action="missing") is False

    def test_run_helper_success_failure_and_timeout(self, monkeypatch):
        monkeypatch.setattr(
            kbl_state.subprocess,
            "run",
            lambda *_args, **_kwargs: subprocess.CompletedProcess(
                args=["cmd"],
                returncode=0,
                stdout=" ok \n",
            ),
        )
        assert kbl_state._run(["cmd"]) == "ok"

        monkeypatch.setattr(
            kbl_state.subprocess,
            "run",
            lambda *_args, **_kwargs: subprocess.CompletedProcess(
                args=["cmd"],
                returncode=1,
                stdout="bad",
            ),
        )
        assert kbl_state._run(["cmd"]) is None

        monkeypatch.setattr(
            kbl_state.subprocess,
            "run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                subprocess.TimeoutExpired("cmd", 1)
            ),
        )
        assert kbl_state._run(["cmd"]) is None


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

        monkeypatch.setattr(
            "docking.applets.keyboardlayout.applet.keyboard_settings_command",
            lambda: ["mate-keyboard-properties"],
        )
        monkeypatch.setattr(
            "docking.applets.keyboardlayout.applet.current_layout_command",
            lambda layout_code: ["gkbd-keyboard-display", "-l", layout_code],
        )
        applet = KeyboardLayoutApplet(icon_size=48)
        labels = [item.get_label() for item in applet.get_menu_items()]
        assert labels[0] == "Show Current Layout"
        assert labels[-1] == "Keyboard Settings"
        assert "EN - us" in labels
        assert "BR - br" in labels
        active_items = [
            item
            for item in applet.get_menu_items()
            if hasattr(item, "get_active") and item.get_active()
        ]
        assert [item.get_label() for item in active_items] == ["EN - us"]

    def test_no_layout_detected(self, monkeypatch):
        monkeypatch.setattr(kbl_state, "_run", lambda cmd: None)
        from docking.applets.keyboardlayout.applet import KeyboardLayoutApplet

        applet = KeyboardLayoutApplet(icon_size=48)
        applet.refresh_tooltip()
        assert "No keyboard" in applet.item.name
