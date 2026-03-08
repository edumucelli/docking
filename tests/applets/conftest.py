from __future__ import annotations

import gi
import pytest

gi.require_version("Gtk", "3.0")
gi.require_version("Wnck", "3.0")
from gi.repository import (
    Gtk,  # noqa: E402
    Wnck,  # noqa: E402
)


class _FakeMenu:
    def __init__(self) -> None:
        self.children: list[object] = []

    def append(self, item) -> None:
        self.children.append(item)


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

    def connect(self, signal: str, callback, *args) -> None:
        self._signals.setdefault(signal, []).append((callback, args))

    def set_submenu(self, submenu) -> None:
        self._submenu = submenu

    def get_submenu(self):
        return self._submenu

    def add(self, child) -> None:
        self._child = child


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
    monkeypatch.setattr(Gtk, "Menu", _FakeMenu, raising=False)
    monkeypatch.setattr(Gtk, "MenuItem", _FakeMenuItem, raising=False)
    monkeypatch.setattr(Gtk, "CheckMenuItem", _FakeCheckMenuItem, raising=False)
    monkeypatch.setattr(Gtk, "RadioMenuItem", _FakeRadioMenuItem, raising=False)
    monkeypatch.setattr(Gtk, "SeparatorMenuItem", _FakeSeparatorMenuItem, raising=False)
    yield


@pytest.fixture(autouse=True)
def _default_no_wnck_screen(monkeypatch):
    monkeypatch.setattr(Wnck.Screen, "get_default", lambda: None, raising=False)
    yield
