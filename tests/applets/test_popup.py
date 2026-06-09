from __future__ import annotations

from unittest.mock import MagicMock

import docking.applets.popup as popup
from docking.ui.display import ScreenPosition


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


class _FakePopupWindow:
    def __init__(self) -> None:
        self.child = "old"
        self.removed = None
        self.added = None
        self.shown = False
        self.moved_to = None
        self.decorated = None
        self.skip_taskbar = None
        self.resizable = None
        self.type_hint = None
        self.accept_focus = None
        self.focus_on_map = None

    def set_decorated(self, value: bool) -> None:
        self.decorated = value

    def set_skip_taskbar_hint(self, value: bool) -> None:
        self.skip_taskbar = value

    def set_resizable(self, value: bool) -> None:
        self.resizable = value

    def set_accept_focus(self, value: bool) -> None:
        self.accept_focus = value

    def set_focus_on_map(self, value: bool) -> None:
        self.focus_on_map = value

    def set_type_hint(self, hint) -> None:
        self.type_hint = hint

    def get_child(self):
        return self.child

    def remove(self, child) -> None:
        self.removed = child
        self.child = None

    def add(self, child) -> None:
        self.added = child
        self.child = child

    def show_all(self) -> None:
        self.shown = True

    def get_preferred_size(self):
        size = type("Size", (), {"width": 80, "height": 40})()
        return None, size

    def get_screen(self):
        return type(
            "Screen",
            (),
            {"get_width": lambda _self: 100, "get_height": lambda _self: 80},
        )()

    def move(self, x: int, y: int) -> None:
        self.moved_to = (x, y)


class _FakeDialogBox:
    def __init__(self) -> None:
        self.spacing = None
        self.margins: list[tuple[str, int]] = []

    def set_spacing(self, value: int) -> None:
        self.spacing = value

    def set_margin_start(self, value: int) -> None:
        self.margins.append(("start", value))

    def set_margin_end(self, value: int) -> None:
        self.margins.append(("end", value))

    def set_margin_top(self, value: int) -> None:
        self.margins.append(("top", value))

    def set_margin_bottom(self, value: int) -> None:
        self.margins.append(("bottom", value))


class _FakeDialog:
    def __init__(self) -> None:
        self.box = _FakeDialogBox()
        self.size = None
        self.position = None
        self.resizable = None
        self.default_response = None
        self.skip_taskbar = None
        self.skip_pager = None
        self.buttons: list[object] = []

    def set_skip_taskbar_hint(self, value: bool) -> None:
        self.skip_taskbar = value

    def set_skip_pager_hint(self, value: bool) -> None:
        self.skip_pager = value

    def set_default_size(self, width: int, height: int) -> None:
        self.size = (width, height)

    def set_position(self, position) -> None:
        self.position = position

    def set_resizable(self, value: bool) -> None:
        self.resizable = value

    def set_default_response(self, response) -> None:
        self.default_response = response

    def get_content_area(self):
        return self.box

    def add_buttons(self, *args) -> None:
        self.buttons.extend(args)


class _FakeCaptureScreen:
    def get_rgba_visual(self):
        return "rgba"

    def get_width(self) -> int:
        return 800

    def get_height(self) -> int:
        return 600


class _FakeCaptureGdkWindow:
    def __init__(self) -> None:
        self.cursor = None

    def set_cursor(self, cursor) -> None:
        self.cursor = cursor


class _FakeCaptureOverlay:
    def __init__(self, **_kwargs) -> None:
        self.screen = _FakeCaptureScreen()
        self.gdk_window = _FakeCaptureGdkWindow()
        self.decorated = None
        self.paintable = None
        self.visual = None
        self.size = None
        self.moved_to = None
        self.connections: list[str] = []
        self.events = None
        self.shown = False
        self.destroyed = False

    def set_decorated(self, value: bool) -> None:
        self.decorated = value

    def set_app_paintable(self, value: bool) -> None:
        self.paintable = value

    def get_screen(self):
        return self.screen

    def set_visual(self, visual) -> None:
        self.visual = visual

    def set_default_size(self, width: int, height: int) -> None:
        self.size = (width, height)

    def move(self, x: int, y: int) -> None:
        self.moved_to = (x, y)

    def connect(self, signal: str, _handler) -> None:
        self.connections.append(signal)

    def set_events(self, events) -> None:
        self.events = events

    def show_all(self) -> None:
        self.shown = True

    def get_window(self):
        return self.gdk_window

    def destroy(self) -> None:
        self.destroyed = True


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


def test_create_popup_window_configures_transient_surface(monkeypatch):
    window = _FakePopupWindow()

    def mock_window_cls(**_kwargs: object) -> _FakePopupWindow:
        return window

    mock_window_cls.list_toplevels = list
    monkeypatch.setattr(popup.Gtk, "Window", mock_window_cls)

    created = popup.create_popup_window()

    assert created is window
    assert window.decorated is False
    assert window.skip_taskbar is True
    assert window.resizable is False
    assert window.accept_focus is True
    assert window.focus_on_map is True
    assert window.type_hint == popup.Gdk.WindowTypeHint.UTILITY


def test_show_wrapped_popup_replaces_content_and_positions(monkeypatch):
    window = _FakePopupWindow()
    wrapped = object()
    position = MagicMock()
    monkeypatch.setattr(popup, "wrap_popup", lambda _content: wrapped)
    monkeypatch.setattr(popup, "position_popup_near_pointer", position)

    popup.show_wrapped_popup(window=window, content="content", gap_px=24)

    assert window.removed == "old"
    assert window.added is wrapped
    assert window.shown is True
    position.assert_called_once_with(window=window, gap_px=24)


def test_position_popup_near_pointer_clamps_to_screen(monkeypatch):
    window = _FakePopupWindow()
    monkeypatch.setattr(popup.Gdk.Display, "get_default", lambda: object())
    monkeypatch.setattr(
        popup,
        "get_pointer_position",
        lambda _display: ScreenPosition(x=20, y=15),
    )

    popup.position_popup_near_pointer(window=window, gap_px=20)

    assert window.moved_to == (0, 0)


def test_prepare_dialog_content_applies_standard_layout():
    dialog = _FakeDialog()

    box = popup.prepare_dialog_content(
        dialog=dialog,
        width=320,
        height=120,
        spacing=9,
        margin=14,
        default_response=popup.Gtk.ResponseType.OK,
        resizable=False,
    )

    assert box is dialog.box
    assert dialog.skip_taskbar is True
    assert dialog.skip_pager is True
    assert dialog.size == (320, 120)
    assert dialog.position == popup.Gtk.WindowPosition.MOUSE
    assert dialog.resizable is False
    assert dialog.default_response == popup.Gtk.ResponseType.OK
    assert dialog.box.spacing == 9
    assert dialog.box.margins == [
        ("start", 14),
        ("end", 14),
        ("top", 14),
        ("bottom", 14),
    ]


def test_add_cancel_ok_buttons_uses_cancel_then_ok_order():
    dialog = _FakeDialog()

    popup.add_cancel_ok_buttons(dialog=dialog)

    assert dialog.buttons == [
        popup.Gtk.STOCK_CANCEL,
        popup.Gtk.ResponseType.CANCEL,
        popup.Gtk.STOCK_OK,
        popup.Gtk.ResponseType.OK,
    ]


def test_draw_transparent_capture_overlay():
    cr = MagicMock()

    assert popup.draw_transparent_capture_overlay(MagicMock(), cr) is True
    cr.set_source_rgba.assert_called_once_with(0, 0, 0, 0.01)
    cr.paint.assert_called_once_with()


def test_create_capture_overlay_configures_fullscreen_grab(monkeypatch):
    overlay = _FakeCaptureOverlay()
    seat = MagicMock()
    display = MagicMock()
    display.get_default_seat.return_value = seat
    monkeypatch.setattr(popup.Gtk, "Window", lambda **_kwargs: overlay)
    monkeypatch.setattr(popup.Gdk.Display, "get_default", lambda: display)
    monkeypatch.setattr(popup.Gdk.Cursor, "new_for_display", lambda *_args: "cursor")

    created = popup.create_capture_overlay(
        draw_handler=MagicMock(),
        click_handler=MagicMock(),
        key_handler=MagicMock(),
        cursor_type=popup.Gdk.CursorType.CROSSHAIR,
    )

    assert created is overlay
    assert overlay.decorated is False
    assert overlay.paintable is True
    assert overlay.visual == "rgba"
    assert overlay.size == (800, 600)
    assert overlay.moved_to == (0, 0)
    assert overlay.connections == [
        "draw",
        "button-press-event",
        "key-press-event",
    ]
    assert overlay.gdk_window.cursor == "cursor"
    seat.grab.assert_called_once()


def test_dismiss_capture_overlay_ungrabs_and_destroys(monkeypatch):
    overlay = _FakeCaptureOverlay()
    seat = MagicMock()
    display = MagicMock()
    display.get_default_seat.return_value = seat
    monkeypatch.setattr(popup.Gdk.Display, "get_default", lambda: display)

    popup.dismiss_capture_overlay(overlay)

    seat.ungrab.assert_called_once_with()
    assert overlay.destroyed is True
