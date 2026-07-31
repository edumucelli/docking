"""Global Search shortcut capture and XDG trigger formatting."""

from __future__ import annotations

import re
from typing import ClassVar

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GObject", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GObject, Gtk

from docking.i18n import _

_XDG_MODIFIERS = ("CTRL", "ALT", "SHIFT", "LOGO", "NUM")
_MODIFIER_KEYVALS = {
    Gdk.KEY_Control_L,
    Gdk.KEY_Control_R,
    Gdk.KEY_Alt_L,
    Gdk.KEY_Alt_R,
    Gdk.KEY_Shift_L,
    Gdk.KEY_Shift_R,
    Gdk.KEY_Super_L,
    Gdk.KEY_Super_R,
    Gdk.KEY_Meta_L,
    Gdk.KEY_Meta_R,
    Gdk.KEY_Hyper_L,
    Gdk.KEY_Hyper_R,
}
_KEY_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def shortcut_from_key_event(keyval: int, state: Gdk.ModifierType) -> str | None:
    """Convert a GDK key event to the freedesktop shortcut syntax."""
    if keyval in _MODIFIER_KEYVALS:
        return None
    if keyval == Gdk.KEY_ISO_Left_Tab:
        keyval = Gdk.KEY_Tab
    lowered = Gdk.keyval_to_lower(keyval)
    key_name = Gdk.keyval_name(lowered)
    if not key_name or not _KEY_NAME_RE.fullmatch(key_name):
        return None

    modifiers: list[str] = []
    if state & Gdk.ModifierType.CONTROL_MASK:
        modifiers.append("CTRL")
    if state & Gdk.ModifierType.MOD1_MASK:
        modifiers.append("ALT")
    if state & Gdk.ModifierType.SHIFT_MASK:
        modifiers.append("SHIFT")
    logo_mask = (
        Gdk.ModifierType.SUPER_MASK
        | Gdk.ModifierType.META_MASK
        | Gdk.ModifierType.HYPER_MASK
        | Gdk.ModifierType.MOD4_MASK
    )
    if state & logo_mask:
        modifiers.append("LOGO")
    if not {"CTRL", "ALT", "LOGO"}.intersection(modifiers) and (
        len(key_name) == 1
        or key_name in {"space", "Return", "Tab", "BackSpace", "Delete"}
    ):
        return None
    return "+".join((*modifiers, key_name))


def shortcut_label(shortcut: str) -> str:
    """Return a compact user-facing label for an XDG shortcut string."""
    parts = [part.strip() for part in shortcut.split("+") if part.strip()]
    if not parts:
        return _("Not set")
    labels = {
        "CTRL": _("Ctrl"),
        "ALT": _("Alt"),
        "SHIFT": _("Shift"),
        "LOGO": _("Super"),
        "NUM": _("Num"),
        "space": _("Space"),
        "Return": _("Enter"),
        "Escape": _("Escape"),
        "Tab": _("Tab"),
        "BackSpace": _("Backspace"),
    }
    rendered = [
        labels.get(part.upper(), labels.get(part, part))
        if part.upper() in _XDG_MODIFIERS
        else labels.get(part, part)
        for part in parts
    ]
    return "+".join(rendered)


class ShortcutCaptureButton(Gtk.Button):
    """A button that captures the next non-modifier key combination."""

    __gsignals__: ClassVar[dict[str, tuple[object, ...]]] = {
        "shortcut-changed": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (str,),
        ),
        "capture-started": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (),
        ),
        "capture-ended": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (),
        ),
    }

    def __init__(self) -> None:
        super().__init__()
        self._shortcut = ""
        self._capturing = False
        self.connect("clicked", self._on_clicked)
        self.connect("key-press-event", self._on_key_press)
        self.connect("focus-out-event", self._on_focus_out)
        self.get_accessible().set_name(_("Search shortcut"))
        self._refresh_label()

    @property
    def capturing(self) -> bool:
        return self._capturing

    def get_shortcut(self) -> str:
        return self._shortcut

    def set_shortcut(self, shortcut: str) -> None:
        self._shortcut = str(shortcut).strip()
        if not self._capturing:
            self._refresh_label()

    def begin_capture(self) -> None:
        if self._capturing:
            return
        self._capturing = True
        self.set_label(_("Press shortcut..."))
        self.emit("capture-started")
        self.grab_focus()

    def cancel_capture(self) -> None:
        if not self._capturing:
            return
        self._capturing = False
        self._refresh_label()
        self.emit("capture-ended")

    def _commit(self, shortcut: str) -> None:
        self._shortcut = shortcut
        self._capturing = False
        self._refresh_label()
        self.emit("shortcut-changed", shortcut)
        self.emit("capture-ended")

    def _refresh_label(self) -> None:
        self.set_label(shortcut_label(self._shortcut))

    def _on_clicked(self, _button: Gtk.Button) -> None:
        if self._capturing:
            self.cancel_capture()
        else:
            self.begin_capture()

    def _on_key_press(self, _button: Gtk.Button, event: Gdk.EventKey) -> bool:
        if not self._capturing:
            return False
        if event.keyval == Gdk.KEY_Escape:
            self.cancel_capture()
            return True
        shortcut = shortcut_from_key_event(event.keyval, event.state)
        if shortcut is None:
            return True
        self._commit(shortcut)
        return True

    def _on_focus_out(self, *_args: object) -> bool:
        if self._capturing:
            self.cancel_capture()
        return False


__all__ = [
    "ShortcutCaptureButton",
    "shortcut_from_key_event",
    "shortcut_label",
]
