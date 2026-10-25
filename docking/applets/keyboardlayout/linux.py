# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Linux keyboard layout discovery, commands and desktop integrations.

Detects GNOME, IBus, Fcitx5, MATE and the setxkbmap fallback. The active desktop
determines priority; existing session-specific configuration stays in place.

IBus engines use ``xkb:LAYOUT:VARIANT:LANG`` (e.g. ``xkb:br::por``).
Fcitx5 input methods use ``keyboard-LAYOUT`` (e.g. ``keyboard-br``).
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import threading
from pathlib import Path

from docking.applets.keyboardlayout.backend import LayoutBackend
from docking.applets.keyboardlayout.state import LayoutState
from docking.log import get_logger, with_context
from docking.platform import environment
from docking.platform.environment import flatpak

log = with_context(get_logger(name="keyboardlayout"))

_LAYOUT_RE = re.compile(r"^layout:\s+(.+)$", re.MULTILINE)
_IBUS_XKB_RE = re.compile(r"^xkb:([^:]+):")
_FCITX5_KB_RE = re.compile(r"^keyboard-(.+)$")

_MATE_LAYOUTS_SCHEMA = "org.mate.peripherals-keyboard-xkb.kbd"
_MATE_GENERAL_SCHEMA = "org.mate.peripherals-keyboard-xkb.general"
_MATE_LAYOUTS_KEY = "layouts"
_MATE_MODEL_KEY = "model"
_MATE_OPTIONS_KEY = "options"
_GNOME_INPUT_SOURCES_SCHEMA = "org.gnome.desktop.input-sources"
_GNOME_SOURCES_KEY = "sources"
_GNOME_MRU_SOURCES_KEY = "mru-sources"
_GNOME_CURRENT_KEY = "current"
_KEYBOARD_SETTINGS_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("mate-keyboard-properties",),
    ("gnome-control-center", "keyboard"),
    ("ibus-setup",),
    ("fcitx5-configtool",),
    ("kcmshell6", "kcm_keyboard"),
    ("kcmshell5", "kcm_keyboard"),
)
_LAYOUT_VIEWER_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("gkbd-keyboard-display",),
    ("tecla",),
)


def keyboard_settings_command() -> list[str] | None:
    """Return the first available keyboard-settings command."""
    return _first_available_command(_KEYBOARD_SETTINGS_COMMANDS)


def current_layout_command(layout_code: str) -> list[str] | None:
    """Return the command to show the active keyboard layout, if available."""
    for cmd in _LAYOUT_VIEWER_COMMANDS:
        base_cmd = _available_command(list(cmd))
        if base_cmd is None:
            continue
        if cmd[0] == "gkbd-keyboard-display":
            if not layout_code:
                return None
            return [*base_cmd, "-l", layout_code]
        return base_cmd
    return None


def open_keyboard_settings() -> bool:
    """Launch the desktop keyboard settings screen when available."""
    return _open_command(
        cmd=keyboard_settings_command(),
        action="open_keyboard_settings",
    )


def show_current_layout(layout_code: str) -> bool:
    """Launch the current keyboard layout viewer when available."""
    return _open_command(
        cmd=current_layout_command(layout_code),
        action="show_current_layout",
    )


def _run(cmd: list[str]) -> str | None:
    result = _run_direct(cmd=cmd)
    if result is not None:
        return result
    if environment.is_flatpak() and cmd[:2] != ["flatpak-spawn", "--host"]:
        host_cmd = flatpak.host_command(cmd)
        if host_cmd is not None:
            return _run_direct(cmd=host_cmd)
    return None


def _run_direct(cmd: list[str]) -> str | None:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.bind(action="run_cmd").debug("Failed: %s: %s", cmd, exc)
    return None


def _parse_gsettings_string_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    cleaned = raw.strip()
    if cleaned.startswith("@as "):
        cleaned = cleaned[4:].strip()
    try:
        value = ast.literal_eval(cleaned)
    except (ValueError, SyntaxError) as exc:
        log.debug("Failed to parse gsettings string list %r: %s", raw, exc)
        return []
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str) and entry]


def _parse_gsettings_string(raw: str | None) -> str:
    if not raw:
        return ""
    cleaned = raw.strip()
    try:
        value = ast.literal_eval(cleaned)
    except (ValueError, SyntaxError):
        return cleaned.strip("'\"")
    return value if isinstance(value, str) else ""


def _parse_input_sources(raw: str | None) -> list[tuple[str, str]]:
    if not raw:
        return []
    cleaned = raw.strip()
    if cleaned.startswith("@a("):
        prefix_end = cleaned.find(" ")
        if prefix_end != -1:
            cleaned = cleaned[prefix_end + 1 :].strip()
    try:
        value = ast.literal_eval(cleaned)
    except (ValueError, SyntaxError) as exc:
        log.debug("Failed to parse input sources %r: %s", raw, exc)
        return []
    sources: list[tuple[str, str]] = []
    if not isinstance(value, list):
        return sources
    for entry in value:
        if (
            isinstance(entry, tuple)
            and len(entry) == 2
            and all(isinstance(part, str) for part in entry)
        ):
            sources.append((entry[0], entry[1]))
    return sources


def _source_layout_code(source_id: str) -> str:
    if "+" in source_id:
        return source_id.split("+", 1)[0]
    return source_id


class LinuxLayoutBackend(LayoutBackend):
    def settings_available(self) -> bool:
        return keyboard_settings_command() is not None

    def viewer_available(self, layout_code: str) -> bool:
        return current_layout_command(layout_code) is not None

    def open_settings(self) -> bool:
        return open_keyboard_settings()

    def show_layout(self, layout_code: str) -> bool:
        return show_current_layout(layout_code)


class GnomeBackend(LinuxLayoutBackend):
    """Reads GNOME input sources and switches via GNOME Shell D-Bus."""

    name = "gnome"

    def is_available(self) -> bool:
        if not environment.is_gnome_session():
            return False
        return bool(self._sources())

    def query(self) -> LayoutState:
        sources = self._sources()
        available = [
            _source_layout_code(source_id)
            for source_type, source_id in sources
            if source_type == "xkb"
        ]
        active = self._active_source_code(sources=sources)
        if not active and available:
            active = available[0]
        return LayoutState(active=active, available=available)

    def switch(self, layout_code: str, *, context: str = "") -> None:
        for idx, (source_type, source_id) in enumerate(self._sources()):
            if source_type != "xkb":
                continue
            if _source_layout_code(source_id) != layout_code:
                continue
            _run(
                cmd=[
                    "gsettings",
                    "set",
                    _GNOME_INPUT_SOURCES_SCHEMA,
                    _GNOME_CURRENT_KEY,
                    str(idx),
                ]
            )
            gdbus = shutil.which("gdbus")
            if not gdbus:
                return
            script = (
                "imports.ui.status.keyboard"
                f".getInputSourceManager().inputSources[{idx}].activate()"
            )
            _run(
                cmd=[
                    gdbus,
                    "call",
                    "--session",
                    "--dest",
                    "org.gnome.Shell",
                    "--object-path",
                    "/org/gnome/Shell",
                    "--method",
                    "org.gnome.Shell.Eval",
                    script,
                ]
            )
            return

    def _sources(self) -> list[tuple[str, str]]:
        raw = _run(
            cmd=["gsettings", "get", _GNOME_INPUT_SOURCES_SCHEMA, _GNOME_SOURCES_KEY]
        )
        return _parse_input_sources(raw=raw)

    def _active_source_code(self, *, sources: list[tuple[str, str]]) -> str:
        current_index = self._current_index()
        if 0 <= current_index < len(sources):
            source_type, source_id = sources[current_index]
            if source_type == "xkb":
                return _source_layout_code(source_id)
        raw = _run(
            cmd=[
                "gsettings",
                "get",
                _GNOME_INPUT_SOURCES_SCHEMA,
                _GNOME_MRU_SOURCES_KEY,
            ]
        )
        mru = _parse_input_sources(raw=raw)
        if mru:
            source_type, source_id = mru[0]
            if source_type == "xkb":
                return _source_layout_code(source_id)
        for source_type, source_id in sources:
            if source_type == "xkb":
                return _source_layout_code(source_id)
        return ""

    def _current_index(self) -> int:
        raw = _run(
            cmd=["gsettings", "get", _GNOME_INPUT_SOURCES_SCHEMA, _GNOME_CURRENT_KEY]
        )
        if not raw:
            return -1
        match = re.search(r"(\d+)", raw)
        if not match:
            return -1
        return int(match.group(1))


def _ibus_layout_code(engine: str) -> str:
    """Extract layout code from IBus engine (e.g. 'xkb:br::por' → 'br')."""
    match = _IBUS_XKB_RE.match(engine)
    return str(match.group(1)) if match else engine


class IBusBackend(LinuxLayoutBackend):
    """Reads/switches layouts via ``ibus`` CLI and dconf."""

    name = "ibus"

    def is_available(self) -> bool:
        return _run(cmd=["ibus", "engine"]) is not None

    def query(self) -> LayoutState:
        active_engine = _run(cmd=["ibus", "engine"])
        if active_engine is None:
            return LayoutState(active="", available=[])
        engines = self._get_engines()
        if not engines:
            engines = [active_engine]
        active = _ibus_layout_code(engine=active_engine)
        available = [_ibus_layout_code(engine=e) for e in engines]
        return LayoutState(active=active, available=available)

    def switch(self, layout_code: str, *, context: str = "") -> None:
        for engine in self._get_engines():
            if _ibus_layout_code(engine=engine) == layout_code:
                _run(cmd=["ibus", "engine", engine])
                return
        _run(cmd=["ibus", "engine", f"xkb:{layout_code}::eng"])

    def _get_engines(self) -> list[str]:
        out = _run(
            cmd=[
                "dconf",
                "read",
                "/desktop/ibus/general/preload-engines",
            ]
        )
        if not out:
            return []
        cleaned = out.strip("[]").replace("'", "").replace('"', "")
        return [e.strip() for e in cleaned.split(",") if e.strip()]


def _fcitx5_layout_code(im_name: str) -> str:
    """Extract layout code from Fcitx5 IM (e.g. 'keyboard-br' → 'br')."""
    match = _FCITX5_KB_RE.match(im_name)
    return str(match.group(1)) if match else im_name


class Fcitx5Backend(LinuxLayoutBackend):
    """Reads/switches layouts via ``fcitx5-remote`` and profile file."""

    name = "fcitx5"

    def is_available(self) -> bool:
        return _run(cmd=["fcitx5-remote", "-n"]) is not None

    def query(self) -> LayoutState:
        active_im = _run(cmd=["fcitx5-remote", "-n"])
        if active_im is None:
            return LayoutState(active="", available=[])
        ims = self._get_ims()
        if not ims:
            ims = [active_im]
        active = _fcitx5_layout_code(im_name=active_im)
        available = [_fcitx5_layout_code(im_name=im) for im in ims]
        return LayoutState(active=active, available=available)

    def switch(self, layout_code: str, *, context: str = "") -> None:
        for im in self._get_ims():
            if _fcitx5_layout_code(im_name=im) == layout_code:
                _run(cmd=["fcitx5-remote", "-s", im])
                return
        _run(cmd=["fcitx5-remote", "-s", f"keyboard-{layout_code}"])

    def _get_ims(self) -> list[str]:
        """Read input methods from ``~/.config/fcitx5/profile``."""
        profile = Path.home() / ".config" / "fcitx5" / "profile"
        if not profile.exists():
            return []
        try:
            text = profile.read_text()
        except OSError as exc:
            log.debug("Failed to read fcitx5 profile %s: %s", profile, exc)
            return []
        ims: list[str] = []
        in_items = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("[Groups/0/Items/"):
                in_items = True
                continue
            if stripped.startswith("[") and in_items:
                if not stripped.startswith("[Groups/0/Items/"):
                    break
                continue
            if in_items and stripped.startswith("Name="):
                ims.append(stripped.split("=", 1)[1])
        return ims


class MateBackend(LinuxLayoutBackend):
    """Reads configured layouts from MATE and switches with setxkbmap."""

    name = "mate"

    def is_available(self) -> bool:
        if not environment.is_mate_session():
            return False
        return bool(self._layouts())

    def query(self) -> LayoutState:
        available = self._layouts()
        active = XkbBackend().query().active
        if not active and available:
            active = available[0]
        return LayoutState(active=active, available=available)

    def switch(self, layout_code: str, *, context: str = "") -> None:
        cmd = ["setxkbmap"]
        model = self._model()
        if model:
            cmd.extend(["-model", model])
        layouts = self._layouts()
        if layout_code in layouts:
            target_index = layouts.index(layout_code)
            active_layouts = layouts[target_index:] + layouts[:target_index]
            cmd.extend(["-layout", ",".join(active_layouts)])
        else:
            cmd.extend(["-layout", layout_code])
        options = self._options()
        if options:
            cmd.extend(["-option", ",".join(options)])
        _run(cmd=cmd)

    def _layouts(self) -> list[str]:
        raw = _run(cmd=["gsettings", "get", _MATE_LAYOUTS_SCHEMA, _MATE_LAYOUTS_KEY])
        return _parse_gsettings_string_list(raw=raw)

    def _model(self) -> str:
        raw = _run(cmd=["gsettings", "get", _MATE_LAYOUTS_SCHEMA, _MATE_MODEL_KEY])
        return _parse_gsettings_string(raw=raw)

    def _options(self) -> list[str]:
        raw = _run(cmd=["gsettings", "get", _MATE_LAYOUTS_SCHEMA, _MATE_OPTIONS_KEY])
        return _parse_gsettings_string_list(raw=raw)


class XkbBackend(LinuxLayoutBackend):
    """Reads/switches layouts via ``setxkbmap``."""

    name = "xkb"

    def is_available(self) -> bool:
        return _run(cmd=["setxkbmap", "-query"]) is not None

    def query(self) -> LayoutState:
        out = _run(cmd=["setxkbmap", "-query"])
        if not out:
            return LayoutState(active="", available=[])
        layout_match = _LAYOUT_RE.search(out)
        layouts = layout_match.group(1).strip().split(",") if layout_match else []
        active = layouts[0] if layouts else ""
        return LayoutState(active=active, available=layouts)

    def switch(self, layout_code: str, *, context: str = "") -> None:
        _run(cmd=["setxkbmap", "-layout", layout_code])


def detect_backend() -> LayoutBackend:
    """Return the first available backend, falling back to XKB."""
    backends: list[type[LayoutBackend]]
    if environment.is_gnome_session():
        backends = [
            GnomeBackend,
            IBusBackend,
            Fcitx5Backend,
            MateBackend,
            XkbBackend,
        ]
    else:
        if environment.is_mate_session():
            backends = [
                MateBackend,
                IBusBackend,
                Fcitx5Backend,
                GnomeBackend,
                XkbBackend,
            ]
        else:
            backends = [
                IBusBackend,
                Fcitx5Backend,
                MateBackend,
                GnomeBackend,
                XkbBackend,
            ]
    for cls in backends:
        backend = cls()
        if backend.is_available():
            return backend
    return XkbBackend()


def _first_available_command(
    candidates: tuple[tuple[str, ...], ...],
) -> list[str] | None:
    for cmd in candidates:
        command = _available_command(list(cmd))
        if command is not None:
            return command
    return None


def _available_command(cmd: list[str]) -> list[str] | None:
    if shutil.which(cmd[0]):
        return cmd
    if flatpak.host_command_available(cmd[0]):
        return flatpak.host_command(cmd)
    return None


def _open_command(*, cmd: list[str] | None, action: str) -> bool:
    if cmd is None:
        return False
    try:
        subprocess.Popen(cmd, start_new_session=True)
    except OSError as exc:
        log.bind(action=action).warning("Failed to run %s: %s", cmd, exc)
        return False
    return True


class LinuxKeyboardBackend(LinuxLayoutBackend):
    """Lazy Linux selection keeps daemon/CLI discovery off the GTK thread."""

    name = "linux"

    def __init__(self) -> None:
        self._delegate: LayoutBackend | None = None
        self._lock = threading.Lock()
        self._stopped = threading.Event()

    def is_available(self) -> bool:
        return bool(self.query().available)

    def query(self) -> LayoutState:
        with self._lock:
            if self._stopped.is_set():
                return LayoutState("", [])
            if self._delegate is None:
                self._delegate = detect_backend()
            return self._delegate.query()

    def switch(self, layout_code: str, *, context: str = "") -> None:
        with self._lock:
            if self._stopped.is_set():
                return
            if self._delegate is None:
                self._delegate = detect_backend()
            self._delegate.switch(layout_code, context=context)

    def stop(self) -> None:
        self._stopped.set()
