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

"""Platform-independent input-source state, labels and cycling."""

from __future__ import annotations

from typing import NamedTuple

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


class LayoutInfo(NamedTuple):
    code: str
    label: str
    name: str


class LayoutState(NamedTuple):
    active: str
    available: list[str]
    details: tuple[LayoutInfo, ...] = ()
    context: str = ""


def cycle_layout(current: str, available: list[str]) -> str:
    """Return the next layout in the cycle."""
    if not available:
        return current
    try:
        idx = available.index(current)
        return available[(idx + 1) % len(available)]
    except ValueError:
        return available[0]


def layout_label(code: str, state: LayoutState | None = None) -> str:
    """Short uppercase label for icon display (e.g., 'us' → 'EN')."""
    if state is not None:
        for info in state.details:
            if info.code == code:
                return info.label
    return _CODE_TO_LABEL.get(code, code.upper()[:2])


def layout_display_name(code: str, state: LayoutState | None = None) -> str:
    """Human-readable name for a layout code."""
    if state is not None:
        for info in state.details:
            if info.code == code:
                return info.name
    return LAYOUT_NAMES.get(code, code)


def tooltip_text(active: str, state: LayoutState | None = None) -> str:
    """Build tooltip string showing current layout name."""
    return layout_display_name(code=active, state=state)
