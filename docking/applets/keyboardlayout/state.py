"""Pure state logic for keyboard layout applet - no GTK/Cairo.

Supports three backends (tried in order):
  1. IBus - reads engines via ``ibus engine`` / dconf
  2. Fcitx5 - reads via ``fcitx5-remote -n`` / profile file
  3. setxkbmap (fallback) - parses ``setxkbmap -query``

IBus engines use ``xkb:LAYOUT:VARIANT:LANG`` (e.g. ``xkb:br::por``).
Fcitx5 input methods use ``keyboard-LAYOUT`` (e.g. ``keyboard-br``).
"""

from __future__ import annotations

import re
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import NamedTuple

from docking.log import get_logger, with_context

log = with_context(get_logger(name="keyboardlayout"))

_LAYOUT_RE = re.compile(r"^layout:\s+(.+)$", re.MULTILINE)
_IBUS_XKB_RE = re.compile(r"^xkb:([^:]+):")
_FCITX5_KB_RE = re.compile(r"^keyboard-(.+)$")

LAYOUT_NAMES: dict[str, str] = {
    "us": "English (US)",
    "gb": "English (UK)",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "br": "Portuguese (BR)",
    "ru": "Russian",
    "ua": "Ukrainian",
    "pl": "Polish",
    "cz": "Czech",
    "sk": "Slovak",
    "hu": "Hungarian",
    "ro": "Romanian",
    "bg": "Bulgarian",
    "hr": "Croatian",
    "rs": "Serbian",
    "si": "Slovenian",
    "nl": "Dutch",
    "be": "Belgian",
    "dk": "Danish",
    "fi": "Finnish",
    "se": "Swedish",
    "no": "Norwegian",
    "is": "Icelandic",
    "ee": "Estonian",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "gr": "Greek",
    "tr": "Turkish",
    "il": "Hebrew",
    "ar": "Arabic",
    "in": "Hindi",
    "cn": "Chinese",
    "jp": "Japanese",
    "kr": "Korean",
    "th": "Thai",
    "vn": "Vietnamese",
    "latam": "Latin American",
    "ca": "Canadian",
    "ch": "Swiss",
    "at": "Austrian",
    "ie": "Irish",
}

_CODE_TO_LABEL: dict[str, str] = {
    "us": "EN",
    "gb": "EN",
    "es": "ES",
    "fr": "FR",
    "de": "DE",
    "it": "IT",
    "pt": "PT",
    "br": "BR",
    "ru": "RU",
    "ua": "UA",
    "pl": "PL",
    "cz": "CZ",
    "sk": "SK",
    "hu": "HU",
    "ro": "RO",
    "bg": "BG",
    "hr": "HR",
    "rs": "RS",
    "si": "SI",
    "nl": "NL",
    "be": "BE",
    "dk": "DK",
    "fi": "FI",
    "se": "SE",
    "no": "NO",
    "is": "IS",
    "ee": "ET",
    "lt": "LT",
    "lv": "LV",
    "gr": "GR",
    "tr": "TR",
    "il": "HE",
    "ar": "AR",
    "in": "HI",
    "cn": "ZH",
    "jp": "JA",
    "kr": "KO",
    "th": "TH",
    "vn": "VN",
    "latam": "LA",
    "ca": "CA",
    "ch": "CH",
    "at": "AT",
    "ie": "IE",
}


class LayoutState(NamedTuple):
    active: str
    available: list[str]


def _run(cmd: list[str]) -> str | None:
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


class LayoutBackend(ABC):
    """Common interface for keyboard layout backends."""

    name: str

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this backend's daemon/tool is running."""

    @abstractmethod
    def query(self) -> LayoutState:
        """Return the current layout state."""

    @abstractmethod
    def switch(self, layout_code: str) -> None:
        """Switch to the given layout code."""


def _ibus_layout_code(engine: str) -> str:
    """Extract layout code from IBus engine (e.g. 'xkb:br::por' → 'br')."""
    match = _IBUS_XKB_RE.match(engine)
    return match.group(1) if match else engine


class IBusBackend(LayoutBackend):
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

    def switch(self, layout_code: str) -> None:
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
    return match.group(1) if match else im_name


class Fcitx5Backend(LayoutBackend):
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

    def switch(self, layout_code: str) -> None:
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


class XkbBackend(LayoutBackend):
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

    def switch(self, layout_code: str) -> None:
        _run(cmd=["setxkbmap", "-layout", layout_code])


_BACKENDS: list[type[LayoutBackend]] = [
    IBusBackend,
    Fcitx5Backend,
    XkbBackend,
]


def detect_backend() -> LayoutBackend:
    """Return the first available backend, falling back to XKB."""
    for cls in _BACKENDS:
        backend = cls()
        if backend.is_available():
            return backend
    return XkbBackend()


def cycle_layout(current: str, available: list[str]) -> str:
    """Return the next layout in the cycle."""
    if not available:
        return current
    try:
        idx = available.index(current)
        return available[(idx + 1) % len(available)]
    except ValueError as exc:
        log.debug(
            "Current layout %r missing from available list %r: %s",
            current,
            available,
            exc,
        )
        return available[0]


def layout_label(code: str) -> str:
    """Short uppercase label for icon display (e.g., 'us' → 'EN')."""
    return _CODE_TO_LABEL.get(code, code.upper()[:2])


def layout_display_name(code: str) -> str:
    """Human-readable name for a layout code."""
    return LAYOUT_NAMES.get(code, code)


def tooltip_text(active: str) -> str:
    """Build tooltip string showing current layout name."""
    return layout_display_name(code=active)
