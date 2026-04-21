from __future__ import annotations

from unittest.mock import MagicMock

import docking.applets.popup as popup


class _FakeCssProvider:
    def __init__(self) -> None:
        self.loaded = None

    def load_from_data(self, data: bytes) -> None:
        self.loaded = data


class _FakeStyleContext:
    def __init__(self) -> None:
        self.classes: list[str] = []

    def add_class(self, name: str) -> None:
        self.classes.append(name)


class _FakeFrame:
    def __init__(self) -> None:
        self.shadow_type = None
        self.child = None
        self.style_context = _FakeStyleContext()

    def set_shadow_type(self, shadow_type) -> None:
        self.shadow_type = shadow_type

    def get_style_context(self) -> _FakeStyleContext:
        return self.style_context

    def add(self, child) -> None:
        self.child = child


def test_ensure_popup_css_returns_without_screen(monkeypatch):
    add_provider = MagicMock()
    monkeypatch.setattr(popup.Gdk.Screen, "get_default", lambda: None)
    monkeypatch.setattr(popup.Gtk.StyleContext, "add_provider_for_screen", add_provider)
    popup._popup_css_provider = None

    popup.ensure_popup_css()

    assert popup._popup_css_provider is None
    add_provider.assert_not_called()


def test_ensure_popup_css_installs_provider_once(monkeypatch):
    provider = _FakeCssProvider()
    screen = object()
    add_provider = MagicMock()
    monkeypatch.setattr(popup.Gdk.Screen, "get_default", lambda: screen)
    monkeypatch.setattr(popup.Gtk, "CssProvider", lambda: provider)
    monkeypatch.setattr(popup.Gtk.StyleContext, "add_provider_for_screen", add_provider)
    popup._popup_css_provider = None

    popup.ensure_popup_css()
    popup.ensure_popup_css()

    assert popup._popup_css_provider is provider
    assert provider.loaded == popup._POPUP_CSS
    add_provider.assert_called_once_with(
        screen,
        provider,
        popup.Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


def test_wrap_popup_applies_theme_class_and_adds_child(monkeypatch):
    content = object()
    frame = _FakeFrame()
    ensure_popup_css = MagicMock()
    monkeypatch.setattr(popup, "ensure_popup_css", ensure_popup_css)
    monkeypatch.setattr(popup.Gtk, "Frame", lambda: frame)

    wrapped = popup.wrap_popup(content)

    assert wrapped is frame
    assert frame.shadow_type == popup.Gtk.ShadowType.NONE
    assert frame.style_context.classes == [popup._POPUP_CLASS]
    assert frame.child is content
    ensure_popup_css.assert_called_once_with()
