from __future__ import annotations

import gi
import pytest

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

try:
    gi.require_version("Wnck", "3.0")
    from gi.repository import Wnck
except (ValueError, ImportError):
    Wnck = None


class _FakeMenu:
    def __init__(self) -> None:
        self.children: list[object] = []
        self.destroyed = False

    def append(self, item) -> None:
        self.children.append(item)

    def get_children(self) -> list[object]:
        return list(self.children)

    def destroy(self) -> None:
        self.destroyed = True


class _FakeMenuItem:
    def __init__(self, label: str = "") -> None:
        self._label = label
        self._sensitive = True
        self._submenu = None
        self._signals: dict[str, list[object]] = {}
        self._child = None

    def get_label(self) -> str:
        return self._label

    def set_sensitive(self, value: bool) -> None:
        self._sensitive = value

    def get_sensitive(self) -> bool:
        return self._sensitive

    def set_tooltip_text(self, text: str) -> None:
        self._tooltip_text = text

    def connect(self, signal: str, callback, *args) -> None:
        self._signals.setdefault(signal, []).append((callback, args))

    def activate(self) -> None:
        for callback, args in self._signals.get("activate", []):
            callback(self, *args)

    def set_submenu(self, submenu) -> None:
        self._submenu = submenu

    def get_submenu(self):
        return self._submenu

    def add(self, child) -> None:
        self._child = child

    def get_child(self):
        return self._child

    def remove(self, _child) -> None:
        self._child = None


class _FakeCheckMenuItem(_FakeMenuItem):
    def __init__(self, label: str = "") -> None:
        super().__init__(label)
        self._active = False

    def set_active(self, active: bool) -> None:
        self._active = active

    def get_active(self) -> bool:
        return self._active


class _FakeRadioMenuItem(_FakeCheckMenuItem):
    def join_group(self, first) -> None:
        _ = first


class _FakeSeparatorMenuItem(_FakeMenuItem):
    def __init__(self) -> None:
        super().__init__(label="")


@pytest.fixture(autouse=True)
def _fake_gtk_menu_widgets(monkeypatch):
    # GI resolves subclasses lazily. Load the complete native hierarchy before
    # replacing parents, or a later GI class may inherit a Python test double
    # instead of GObject and reject its own methods on newer PyGObject versions.
    _ = (
        Gtk.Menu,
        Gtk.MenuItem,
        Gtk.CheckMenuItem,
        Gtk.RadioMenuItem,
        Gtk.SeparatorMenuItem,
    )
    monkeypatch.setattr(Gtk, "Menu", _FakeMenu, raising=False)
    monkeypatch.setattr(Gtk, "MenuItem", _FakeMenuItem, raising=False)
    monkeypatch.setattr(Gtk, "CheckMenuItem", _FakeCheckMenuItem, raising=False)
    monkeypatch.setattr(Gtk, "RadioMenuItem", _FakeRadioMenuItem, raising=False)
    monkeypatch.setattr(Gtk, "SeparatorMenuItem", _FakeSeparatorMenuItem, raising=False)
    yield


@pytest.fixture(autouse=True)
def _default_no_wnck_screen(monkeypatch):
    if Wnck is not None:
        monkeypatch.setattr(Wnck.Screen, "get_default", lambda: None, raising=False)
    yield
