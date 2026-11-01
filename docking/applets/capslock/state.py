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

"""Keyboard lock state and presentation for Caps Lock applet."""

from __future__ import annotations

from dataclasses import dataclass

from docking.i18n import _

POLL_INTERVAL_S = 1


@dataclass(frozen=True, slots=True)
class LockKeyState:
    """Current state of the keyboard lock indicators."""

    available: bool
    caps_lock: bool = False
    num_lock: bool = False


def state_label(state: LockKeyState) -> str:
    """Compact icon label for the active lock state."""
    if not state.available:
        return "??"
    if state.caps_lock and state.num_lock:
        return "CN"
    if state.caps_lock:
        return "CAP"
    if state.num_lock:
        return "NUM"
    return "--"


def tooltip_text(state: LockKeyState) -> str:
    """Build user-facing tooltip text."""
    if not state.available:
        return _("Keyboard lock state unavailable")

    return "\n".join(
        [
            _("Caps Lock"),
            _("Caps Lock: {state}").format(state=_on_off(state.caps_lock)),
            _("Num Lock: {state}").format(state=_on_off(state.num_lock)),
        ]
    )


def menu_label(name: str, active: bool) -> str:
    """Build a lock-state menu label."""
    return _("{name}: {state}").format(name=name, state=_on_off(active))


def _on_off(active: bool) -> str:
    return _("On") if active else _("Off")
