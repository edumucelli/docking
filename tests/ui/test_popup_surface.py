"""Tests for the popup surface helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import docking.ui.popup_surface as ps
from docking.ui.popup_surface import (
    STARTUP_POPUP_SURFACE_CLASS,
    STARTUP_POPUP_WINDOW_CLASS,
    configure_transparent_startup_popup_window,
    ensure_startup_popup_css,
    wrap_startup_popup_content,
)


class _FakeStyleContext:
    def __init__(self) -> None:
        self.classes: list[str] = []

    def add_class(self, class_name: str) -> None:
        self.classes.append(class_name)


class _FakeScreen:
    def __init__(self, *, rgba_visual: object = None) -> None:
        self.rgba_visual = rgba_visual
        self.providers: list[tuple[object, int]] = []

    def get_rgba_visual(self) -> object | None:
        return self.rgba_visual

    @staticmethod
    def get_default() -> _FakeScreen | None:
        return _default_screen


class _FakeCssProvider:
    def __init__(self) -> None:
        self.loaded_data: bytes | None = None

    def load_from_data(self, data: bytes) -> None:
        self.loaded_data = data


class _FakeGdkScreen:
    @staticmethod
    def get_default() -> _FakeScreen | None:
        return _default_screen


class _FakeFrame:
    def __init__(self) -> None:
        self.shadow_type = None
        self._child = None
        self._style_context = _FakeStyleContext()

    def set_shadow_type(self, value) -> None:
        self.shadow_type = value

    def add(self, child) -> None:
        self._child = child

    def get_style_context(self) -> _FakeStyleContext:
        return self._style_context


class _FakeLabel:
    def __init__(self, *, label: str = "") -> None:
        self.label = label
        self._style_context = _FakeStyleContext()

    def get_style_context(self) -> _FakeStyleContext:
        return self._style_context


class _FakeWindow:
    def __init__(self, *, screen: _FakeScreen | None = None) -> None:
        self._screen = screen
        self.app_paintable = False
        self.visual = None
        self.bg_override = None
        self._style_context = _FakeStyleContext()

    def set_app_paintable(self, value: bool) -> None:
        self.app_paintable = value

    def get_screen(self) -> _FakeScreen | None:
        return self._screen

    def set_visual(self, visual) -> None:
        self.visual = visual

    def override_background_color(self, state, color) -> None:
        self.bg_override = (state, color)

    def get_style_context(self) -> _FakeStyleContext:
        return self._style_context


class _FakeGtkStyleContext:
    @staticmethod
    def add_provider_for_screen(screen, provider, priority):
        if screen is not None:
            screen.providers.append((provider, priority))


# Module-level mutable state for test control
_default_screen: _FakeScreen | None = None


def _build_gtk_namespace():
    """Build a complete fake Gtk namespace for the popup_surface module."""
    return SimpleNamespace(
        CssProvider=_FakeCssProvider,
        Frame=_FakeFrame,
        ShadowType=SimpleNamespace(NONE="none", OUT="out"),
        StyleContext=_FakeGtkStyleContext,
        STYLE_PROVIDER_PRIORITY_APPLICATION=600,
        StateFlags=SimpleNamespace(NORMAL="normal"),
        Label=_FakeLabel,
    )


def _build_gdk_namespace():
    return SimpleNamespace(
        Screen=_FakeGdkScreen,
        RGBA=lambda r, g, b, a: SimpleNamespace(red=r, green=g, blue=b, alpha=a),
    )


@pytest.fixture(autouse=True)
def _reset_cache_and_screen():
    """Reset module-level state before each test."""
    global _default_screen
    _default_screen = None
    ensure_startup_popup_css.cache_clear()


@pytest.fixture
def fake_gtk(monkeypatch):
    gtk_ns = _build_gtk_namespace()
    monkeypatch.setattr(ps, "Gtk", gtk_ns)
    monkeypatch.setattr(ps, "Gdk", _build_gdk_namespace())
    return gtk_ns


class TestEnsureStartupPopupCss:
    def test_returns_provider_when_screen_available(self, fake_gtk):
        global _default_screen
        _default_screen = _FakeScreen(rgba_visual=object())

        provider = ensure_startup_popup_css()

        assert provider is not None
        assert isinstance(provider, _FakeCssProvider)
        assert provider.loaded_data == ps.STARTUP_POPUP_CSS
        assert len(_default_screen.providers) == 1
        assert _default_screen.providers[0][1] == 600

    def test_returns_none_when_no_screen(self, fake_gtk):
        global _default_screen
        _default_screen = None

        provider = ensure_startup_popup_css()

        assert provider is None

    def test_cached_result_reused(self, fake_gtk):
        global _default_screen
        _default_screen = _FakeScreen(rgba_visual=object())

        first = ensure_startup_popup_css()
        second = ensure_startup_popup_css()

        assert first is second


class TestConfigureTransparentStartupPopupWindow:
    def test_configures_window_with_rgba_visual(self, fake_gtk):
        global _default_screen
        rgba_vis = object()
        _default_screen = _FakeScreen(rgba_visual=rgba_vis)

        window = _FakeWindow(screen=_default_screen)
        configure_transparent_startup_popup_window(window)

        assert window.app_paintable is True
        assert window.visual is rgba_vis
        assert window.bg_override is not None
        assert STARTUP_POPUP_WINDOW_CLASS in window.get_style_context().classes

    def test_configures_window_without_rgba_visual(self, fake_gtk):
        global _default_screen
        _default_screen = _FakeScreen(rgba_visual=None)

        window = _FakeWindow(screen=_default_screen)
        configure_transparent_startup_popup_window(window)

        assert window.app_paintable is True
        assert window.visual is None
        assert window.bg_override is not None
        assert STARTUP_POPUP_WINDOW_CLASS in window.get_style_context().classes

    def test_configures_window_when_screen_is_none(self, fake_gtk):
        global _default_screen
        _default_screen = _FakeScreen(rgba_visual=object())

        window = _FakeWindow(screen=None)
        configure_transparent_startup_popup_window(window)

        assert window.app_paintable is True
        assert window.bg_override is not None
        assert STARTUP_POPUP_WINDOW_CLASS in window.get_style_context().classes


class TestWrapStartupPopupContent:
    def test_wraps_content_in_themed_frame(self, fake_gtk):
        global _default_screen
        _default_screen = _FakeScreen(rgba_visual=object())

        content = _FakeLabel(label="hello")
        frame = wrap_startup_popup_content(content)

        assert isinstance(frame, _FakeFrame)
        assert frame.shadow_type == "none"
        assert frame._child is content
        assert STARTUP_POPUP_SURFACE_CLASS in frame.get_style_context().classes
