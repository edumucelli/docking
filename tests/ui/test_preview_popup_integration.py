"""Integration-style tests for preview popup methods.

This file loads ``docking/ui/preview.py`` in isolation with a typed GI stub so
PreviewPopup is a real Python class during tests.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.core.position import Position


def _load_preview_module():
    root = Path(__file__).resolve().parents[2]
    preview_path = root / "docking" / "ui" / "preview.py"

    original_gi = sys.modules.get("gi")
    original_repo = sys.modules.get("gi.repository")

    class FakeGtkWindow:
        def __init__(self, **_kwargs) -> None:
            return

    fake_gtk = types.SimpleNamespace(
        Window=FakeGtkWindow,
        WindowType=SimpleNamespace(POPUP=1),
        CssProvider=lambda: SimpleNamespace(load_from_data=lambda _data: None),
        StyleContext=SimpleNamespace(
            add_provider_for_screen=lambda *_args, **_kwargs: None
        ),
        STYLE_PROVIDER_PRIORITY_APPLICATION=600,
        Orientation=SimpleNamespace(HORIZONTAL=1, VERTICAL=2),
        IconSize=SimpleNamespace(DIALOG=1),
    )
    fake_gdk = types.SimpleNamespace(
        WindowTypeHint=SimpleNamespace(TOOLTIP=1),
        Screen=SimpleNamespace(get_default=lambda: object()),
        EventMask=SimpleNamespace(BUTTON_PRESS_MASK=1, ENTER_NOTIFY_MASK=2),
        NotifyType=SimpleNamespace(INFERIOR=1),
        CrossingMode=SimpleNamespace(NORMAL=1),
    )
    fake_gdkpixbuf = types.SimpleNamespace(
        Pixbuf=type("Pixbuf", (), {}),
        InterpType=SimpleNamespace(BILINEAR=1),
        Colorspace=SimpleNamespace(RGB=1),
    )
    fake_gdkx11 = types.SimpleNamespace(
        X11Display=SimpleNamespace(get_default=lambda: MagicMock()),
        X11Window=SimpleNamespace(
            foreign_new_for_display=lambda _display, _xid: MagicMock()
        ),
    )
    fake_glib = types.SimpleNamespace(
        Error=Exception,
        timeout_add=lambda *_args, **_kwargs: 1,
        source_remove=lambda *_args, **_kwargs: None,
    )
    fake_pango = types.SimpleNamespace(EllipsizeMode=SimpleNamespace(END=1))
    fake_wnck = types.SimpleNamespace(Window=type("Window", (), {}))

    gi_module = types.ModuleType("gi")
    gi_module.require_version = lambda *_args, **_kwargs: None
    repo_module = types.ModuleType("gi.repository")
    repo_module.Gtk = fake_gtk
    repo_module.Gdk = fake_gdk
    repo_module.GdkPixbuf = fake_gdkpixbuf
    repo_module.GdkX11 = fake_gdkx11
    repo_module.GLib = fake_glib
    repo_module.Pango = fake_pango
    repo_module.Wnck = fake_wnck
    gi_module.repository = repo_module
    sys.modules["gi"] = gi_module
    sys.modules["gi.repository"] = repo_module

    try:
        spec = importlib.util.spec_from_file_location("preview_isolated", preview_path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if original_gi is None:
            sys.modules.pop("gi", None)
        else:
            sys.modules["gi"] = original_gi
        if original_repo is None:
            sys.modules.pop("gi.repository", None)
        else:
            sys.modules["gi.repository"] = original_repo


preview_mod = _load_preview_module()


class FakeScreen:
    def __init__(self, width: int = 320, height: int = 200) -> None:
        self._width = width
        self._height = height

    def get_width(self) -> int:
        return self._width

    def get_height(self) -> int:
        return self._height


class FakeBox:
    def __init__(self, orientation: int, spacing: int) -> None:
        self.orientation = orientation
        self.spacing = spacing
        self.children: list[object] = []

    def set_margin_start(self, _value: int) -> None:
        return

    def set_margin_end(self, _value: int) -> None:
        return

    def set_margin_top(self, _value: int) -> None:
        return

    def set_margin_bottom(self, _value: int) -> None:
        return

    def pack_start(self, child, *_args) -> None:
        self.children.append(child)

    def show_all(self) -> None:
        return

    def get_preferred_size(self):
        return (
            SimpleNamespace(width=0, height=0),
            SimpleNamespace(width=220, height=120),
        )


class FakeStyleContext:
    def add_class(self, _klass: str) -> None:
        return


class FakeEventBox:
    def __init__(self) -> None:
        self._style_context = FakeStyleContext()
        self.child = None

    def get_style_context(self) -> FakeStyleContext:
        return self._style_context

    def set_events(self, _events: int) -> None:
        return

    def connect(self, *_args) -> None:
        return

    def add(self, child) -> None:
        self.child = child


class FakeImage:
    @classmethod
    def new_from_pixbuf(cls, _pixbuf):
        return cls()

    @classmethod
    def new_from_icon_name(cls, _icon_name: str, _icon_size: int):
        return cls()

    def set_size_request(self, _width: int, _height: int) -> None:
        return


class FakeLabel:
    def __init__(self, label: str) -> None:
        self.label = label
        self._style_context = FakeStyleContext()

    def get_style_context(self) -> FakeStyleContext:
        return self._style_context

    def set_ellipsize(self, _value) -> None:
        return

    def set_max_width_chars(self, _value: int) -> None:
        return

    def override_color(self, *_args, **_kwargs) -> None:
        return


def _make_popup():
    popup = object.__new__(preview_mod.PreviewPopup)
    popup._tracker = MagicMock()
    popup._autohide = None
    popup._hide_timer_id = 0
    popup._current_desktop_id = ""
    return popup


class TestPreviewPopupIntegration:
    def test_show_for_item_hides_when_no_windows(self):
        # Given
        popup = _make_popup()
        popup._tracker.get_xids_for.return_value = []
        popup.hide = MagicMock()

        # When
        preview_mod.PreviewPopup.show_for_item(
            popup,
            desktop_id="firefox.desktop",
            anchor_x=10.0,
            icon_w=48.0,
            anchor_y=10.0,
            position=Position.BOTTOM,
        )

        # Then
        popup.hide.assert_called_once()

    def test_show_for_item_builds_content_and_moves(self, monkeypatch):
        # Given
        popup = _make_popup()
        popup._tracker.get_xids_for.return_value = [1, 2]
        popup._tracker.icon_name_for_desktop.return_value = "firefox"
        popup._cancel_hide_timer = MagicMock()
        popup._make_thumbnail_for_xid = MagicMock(return_value=object())
        popup.get_child = MagicMock(return_value=None)
        popup.remove = MagicMock()
        popup.add = MagicMock()
        popup.get_screen = MagicMock(return_value=FakeScreen(width=320, height=200))
        popup.move = MagicMock()
        popup.show_all = MagicMock()
        monkeypatch.setattr(
            preview_mod,
            "Gtk",
            SimpleNamespace(
                Box=FakeBox,
                Orientation=SimpleNamespace(HORIZONTAL=1, VERTICAL=2),
            ),
        )

        # When
        preview_mod.PreviewPopup.show_for_item(
            popup,
            desktop_id="firefox.desktop",
            anchor_x=0.0,
            icon_w=48.0,
            anchor_y=10.0,
            position=Position.BOTTOM,
        )

        # Then
        popup._cancel_hide_timer.assert_called_once()
        popup.add.assert_called_once()
        popup.move.assert_called_once_with(0, 0)
        popup.show_all.assert_called_once()
        assert popup._current_desktop_id == "firefox.desktop"

    def test_make_thumbnail_truncates_label(self, monkeypatch):
        # Given
        popup = _make_popup()
        popup._tracker.get_window_title_for_xid.return_value = "A" * 40
        monkeypatch.setattr(preview_mod, "capture_xid", lambda **_kwargs: None)
        monkeypatch.setattr(
            preview_mod,
            "Gtk",
            SimpleNamespace(
                EventBox=FakeEventBox,
                Box=FakeBox,
                Orientation=SimpleNamespace(HORIZONTAL=1, VERTICAL=2),
                Image=FakeImage,
                Label=FakeLabel,
                IconSize=SimpleNamespace(DIALOG=1),
                StateFlags=SimpleNamespace(NORMAL=1),
            ),
        )
        monkeypatch.setattr(
            preview_mod,
            "Gdk",
            SimpleNamespace(
                EventMask=SimpleNamespace(BUTTON_PRESS_MASK=1, ENTER_NOTIFY_MASK=2),
                RGBA=lambda *_args, **_kwargs: None,
            ),
        )
        monkeypatch.setattr(
            preview_mod,
            "Pango",
            SimpleNamespace(EllipsizeMode=SimpleNamespace(END=1)),
        )

        # When
        widget = preview_mod.PreviewPopup._make_thumbnail_for_xid(
            popup, xid=42, fallback_icon_name="app"
        )

        # Then
        vbox = widget.child
        label = vbox.children[1]
        assert label.label.endswith("\u2026")

    def test_timer_and_leave_branches(self, monkeypatch):
        # Given
        popup = _make_popup()
        popup._autohide = MagicMock()
        popup._schedule_hide = MagicMock()
        inferior = SimpleNamespace(detail=preview_mod.Gdk.NotifyType.INFERIOR, mode=1)
        normal = SimpleNamespace(detail=object(), mode=1)

        # When
        preview_mod.PreviewPopup._on_leave(popup, MagicMock(), inferior)
        preview_mod.PreviewPopup._on_leave(popup, MagicMock(), normal)

        # Then
        popup._schedule_hide.assert_called_once()
        popup._autohide.on_mouse_leave.assert_called_once()

        # Given
        popup._hide_timer_id = 77
        popup.hide = MagicMock()
        remove = MagicMock()
        monkeypatch.setattr(preview_mod.GLib, "source_remove", remove)
        monkeypatch.setattr(preview_mod.GLib, "timeout_add", lambda _ms, _cb: 88)

        # When
        preview_mod.PreviewPopup._schedule_hide(popup, delay_ms=123)
        result = preview_mod.PreviewPopup._do_hide(popup)

        # Then
        remove.assert_called_once_with(77)
        assert popup._hide_timer_id == 0
        assert popup._current_desktop_id == ""
        assert result is False
        popup.hide.assert_called_once()
