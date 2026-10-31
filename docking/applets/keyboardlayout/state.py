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

"""Pure state logic for keyboard layout applet - no GTK/Cairo.

Supports four backends (tried in order):
  1. IBus - reads engines via ``ibus engine`` / dconf
  2. Fcitx5 - reads via ``fcitx5-remote -n`` / profile file
  3. MATE - reads configured layouts from MATE gsettings
  4. setxkbmap (fallback) - parses ``setxkbmap -query``

IBus engines use ``xkb:LAYOUT:VARIANT:LANG`` (e.g. ``xkb:br::por``).
Fcitx5 input methods use ``keyboard-LAYOUT`` (e.g. ``keyboard-br``).
"""

from __future__ import annotations

import ast
import re
from abc import ABC, abstractmethod
from typing import NamedTuple

from docking.log import get_logger, with_context

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


def _ibus_layout_code(engine: str) -> str:
    """Extract layout code from IBus engine (e.g. 'xkb:br::por' → 'br')."""
    match = _IBUS_XKB_RE.match(engine)
    return str(match.group(1)) if match else engine


def _fcitx5_layout_code(im_name: str) -> str:
    """Extract layout code from Fcitx5 IM (e.g. 'keyboard-br' → 'br')."""
    match = _FCITX5_KB_RE.match(im_name)
    return str(match.group(1)) if match else im_name


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
