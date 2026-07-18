"""Integration-style tests for MenuHandler behavior."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

try:
    import gi  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover - fallback for non-GI environments
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    gi_mock.repository.GLib.markup_escape_text.side_effect = lambda text: text
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

import docking.ui.folder.stack as folder_stack_mod
import docking.ui.menu as menu_mod
import docking.ui.stack as stack_mod
from docking.core.items import FILE_KIND, FOLDER_KIND
from docking.platform.backends.base import (
    DisplayServer,
    PreviewImage,
    WindowId,
    WindowSnapshot,
)
from docking.platform.model import DockItem


class FakeFontDescription:
    def __init__(self) -> None:
        self.family = ""
        self.size = 0

    def set_family(self, family: str) -> None:
        self.family = family

    def set_size(self, size: int) -> None:
        self.size = size


class FakePangoLayout:
    def __init__(self) -> None:
        self.text = ""
        self.width = -1
        self.alignment = None

    def set_text(self, text: str, _length: int) -> None:
        self.text = text

    def set_font_description(self, _desc) -> None:
        return

    def set_ellipsize(self, _mode) -> None:
        return

    def set_width(self, width: int) -> None:
        self.width = width

    def set_alignment(self, alignment) -> None:
        self.alignment = alignment

    def get_pixel_extents(self):
        width = max(len(self.text) * 7, 1)
        if self.width > 0:
            width = min(width, max(int(self.width / 1024), 1))
        logical = SimpleNamespace(width=width, height=12)
        ink = SimpleNamespace(width=width, height=12)
        return ink, logical


class FakePangoCairo:
    @staticmethod
    def create_layout(_cr):
        return FakePangoLayout()

    @staticmethod
    def show_layout(_cr, _layout) -> None:
        return


class FakePango:
    SCALE = 1024

    class EllipsizeMode:
        END = 0

    class Alignment:
        CENTER = 0

    FontDescription = FakeFontDescription


def _catalog_entry(*, applet_id, name: str, category=None):
    return menu_mod.AppletMeta(
        id=str(applet_id),
        name=name,
        category=category or menu_mod.AppletCategory.OTHER,
    )


class FakeMenu:
    def __init__(self) -> None:
        self.children: list[FakeMenuItem] = []
        self.shown = False
        self.popup_event = None
        self.popup_widget = None
        self.popdown_called = False
        self.resize_queued = False
        self.resize_checked = False
        self.draw_queued = False
        self._signals: dict[str, list[object]] = {}

    def append(self, item) -> None:
        self.children.append(item)
        if hasattr(item, "parent"):
            item.parent = self

    def get_children(self):
        return list(self.children)

    def remove(self, item) -> None:
        if item in self.children:
            self.children.remove(item)
            if hasattr(item, "parent"):
                item.parent = None

    def connect(self, signal: str, callback, *args) -> None:
        self._signals.setdefault(signal, []).append((callback, args))

    def emit(self, signal: str) -> None:
        for callback, args in self._signals.get(signal, []):
            callback(self, *args)

    def show_all(self) -> None:
        self.shown = True

    def queue_resize(self) -> None:
        self.resize_queued = True

    def check_resize(self) -> None:
        self.resize_checked = True

    def queue_draw(self) -> None:
        self.draw_queued = True

    def popup_at_pointer(self, event) -> None:
        self.popup_event = event

    def popup_at_widget(self, widget, *_args) -> None:
        self.popup_widget = widget

    def popdown(self) -> None:
        self.popdown_called = True


class FakeMenuItem:
    def __init__(self, label: str = "") -> None:
        self._label = label
        self._submenu = None
        self._child = None
        self._sensitive = True
        self.hidden = False
        self.destroyed = False
        self.parent = None
        self.halign = None
        self.hexpand = False
        self.allocation = SimpleNamespace(width=180, height=24)
        self._signals: dict[str, list[tuple[object, tuple[object, ...]]]] = {}

    def connect(self, signal: str, callback, *args) -> None:
        self._signals.setdefault(signal, []).append((callback, args))

    def activate(self) -> None:
        for callback, args in self._signals.get("activate", []):
            callback(self, *args)
        for callback, args in self._signals.get("toggled", []):
            callback(self, *args)

    def emit(self, signal: str, *extra_args):
        result = None
        for callback, args in self._signals.get(signal, []):
            result = callback(self, *extra_args, *args)
        return result

    def set_submenu(self, submenu) -> None:
        self._submenu = submenu

    def get_submenu(self):
        return self._submenu

    def get_label(self) -> str:
        return self._label

    def set_label(self, label: str) -> None:
        self._label = label

    def get_active(self) -> bool:
        return True

    def set_sensitive(self, sensitive: bool) -> None:
        self._sensitive = sensitive

    def get_child(self):
        return self._child

    def remove(self, _child) -> None:
        self._child = None

    def add(self, child) -> None:
        self._child = child
        if hasattr(child, "parent"):
            child.parent = self

    def get_parent(self):
        return self.parent

    def hide(self) -> None:
        self.hidden = True

    def destroy(self) -> None:
        self.destroyed = True

    def get_allocation(self):
        return self.allocation

    def set_halign(self, value) -> None:
        self.halign = value

    def set_hexpand(self, value: bool) -> None:
        self.hexpand = value

    def set_margin_start(self, _value: int) -> None:
        return

    def set_margin_end(self, _value: int) -> None:
        return

    def set_margin_top(self, _value: int) -> None:
        return

    def set_margin_bottom(self, _value: int) -> None:
        return


class FakeCheckMenuItem(FakeMenuItem):
    def __init__(self, label: str = "") -> None:
        super().__init__(label=label)
        self._active = False

    def set_active(self, active: bool) -> None:
        self._active = active

    def get_active(self) -> bool:
        return self._active


class FakeCheckButton(FakeCheckMenuItem):
    pass


class FakeRadioMenuItem(FakeCheckMenuItem):
    def join_group(self, _other) -> None:
        return


class FakeSeparatorMenuItem(FakeMenuItem):
    def __init__(self) -> None:
        super().__init__(label="---")


class FakeImage:
    def __init__(self, pixbuf=None) -> None:
        self.pixel_size = None
        self.pixbuf = pixbuf

    @classmethod
    def new_from_pixbuf(cls, pixbuf):
        return cls(pixbuf=pixbuf)

    def set_pixel_size(self, size: int) -> None:
        self.pixel_size = size


class FakeBox:
    def __init__(self, **_kwargs) -> None:
        self.children: list[object] = []
        self.parent = None
        self.shown = False

    def pack_start(self, child, *_args) -> None:
        self.children.append(child)
        if hasattr(child, "parent"):
            child.parent = self

    def pack_end(self, child, *_args) -> None:
        self.children.append(child)
        if hasattr(child, "parent"):
            child.parent = self

    def set_margin_start(self, _value: int) -> None:
        return

    def set_margin_end(self, _value: int) -> None:
        return

    def set_margin_top(self, _value: int) -> None:
        return

    def set_margin_bottom(self, _value: int) -> None:
        return

    def set_size_request(self, _width: int, _height: int) -> None:
        return

    def show_all(self) -> None:
        self.shown = True

    def get_preferred_size(self):
        height = max(34 * max(len(self.children), 1), 34)
        return (
            SimpleNamespace(width=240, height=height),
            SimpleNamespace(width=240, height=height),
        )


class FakeLabel:
    def __init__(self, label: str = "") -> None:
        self.label = label
        self.xalign = 0.0
        self.max_width_chars = None
        self.ellipsize = None
        self.single_line_mode = False
        self.hexpand = False
        self.parent = None

    def set_xalign(self, xalign: float) -> None:
        self.xalign = xalign

    def set_max_width_chars(self, value: int) -> None:
        self.max_width_chars = value

    def set_ellipsize(self, value) -> None:
        self.ellipsize = value

    def set_single_line_mode(self, value: bool) -> None:
        self.single_line_mode = value

    def set_hexpand(self, value: bool) -> None:
        self.hexpand = value

    def set_margin_start(self, _value: int) -> None:
        return

    def set_margin_end(self, _value: int) -> None:
        return

    def set_margin_top(self, _value: int) -> None:
        return

    def set_margin_bottom(self, _value: int) -> None:
        return


class FakeButton(FakeMenuItem):
    def __init__(self, label: str = "") -> None:
        super().__init__(label=label)
        self.parent = None
        self.relief = None
        self.can_focus = True
        self.hexpand = False
        self.halign = None
        self.size_request = None

    def set_relief(self, relief) -> None:
        self.relief = relief

    def set_can_focus(self, value: bool) -> None:
        self.can_focus = value

    def set_hexpand(self, value: bool) -> None:
        self.hexpand = value

    def set_halign(self, value) -> None:
        self.halign = value

    def get_parent(self):
        return self.parent

    def set_size_request(self, width: int, height: int) -> None:
        self.size_request = (width, height)


class FakeScrolledWindow:
    def __init__(self) -> None:
        self.child = None
        self.policy = None
        self.max_content_height = None

    def set_policy(self, horizontal, vertical) -> None:
        self.policy = (horizontal, vertical)

    def set_propagate_natural_height(self, _value: bool) -> None:
        return

    def set_max_content_height(self, value: int) -> None:
        self.max_content_height = value

    def set_size_request(self, _width: int, height: int) -> None:
        self.max_content_height = height

    def add(self, child) -> None:
        self.child = child
        if hasattr(child, "parent"):
            child.parent = self


class FakeSeparator:
    def __init__(self, **_kwargs) -> None:
        self.parent = None


class FakeComboBoxText:
    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []
        self.active_id = None
        self._signals: dict[str, list[tuple[object, tuple[object, ...]]]] = {}

    def append(self, value: str, display: str) -> None:
        self.items.append((value, display))

    def set_active_id(self, value: str) -> None:
        self.active_id = value

    def get_active_id(self):
        return self.active_id

    def connect(self, signal: str, callback, *args) -> None:
        self._signals.setdefault(signal, []).append((callback, args))


class FakeScreen:
    def get_rgba_visual(self):
        return None

    def get_width(self) -> int:
        return 1920

    def get_height(self) -> int:
        return 1080


class FakeWindow:
    last_created = None

    def __init__(self, **_kwargs) -> None:
        self.child = None
        self.visible = False
        self.moved_to = None
        self.parent = None
        self.screen = FakeScreen()
        FakeWindow.last_created = self

    def get_child(self):
        return self.child

    def set_decorated(self, _value: bool) -> None:
        return

    def set_skip_taskbar_hint(self, _value: bool) -> None:
        return

    def set_resizable(self, _value: bool) -> None:
        return

    def set_type_hint(self, _value) -> None:
        return

    def set_app_paintable(self, _value: bool) -> None:
        return

    def set_visual(self, _visual) -> None:
        return

    def remove(self, _child) -> None:
        self.child = None

    def add(self, child) -> None:
        self.child = child
        if hasattr(child, "parent"):
            child.parent = self

    def show_all(self) -> None:
        self.visible = True

    def hide(self) -> None:
        self.visible = False

    def get_visible(self) -> bool:
        return self.visible

    def move(self, x: int, y: int) -> None:
        self.moved_to = (x, y)

    def get_screen(self):
        return self.screen

    def get_transient_for(self):
        return None


class FakeRevealer:
    def __init__(self) -> None:
        self.child = None
        self.transition_type = None
        self.transition_duration = 0
        self.reveal_child = False

    def set_transition_type(self, value) -> None:
        self.transition_type = value

    def set_transition_duration(self, value: int) -> None:
        self.transition_duration = value

    def set_reveal_child(self, value: bool) -> None:
        self.reveal_child = value

    def get_child(self):
        return self.child

    def remove(self, _child) -> None:
        self.child = None

    def add(self, child) -> None:
        self.child = child
        if hasattr(child, "parent"):
            child.parent = self


class FakeFixed:
    def __init__(self) -> None:
        self.children: list[tuple[object, int, int]] = []
        self.size_request = (1, 1)
        self.parent = None

    def put(self, child, x: int, y: int) -> None:
        self.children.append((child, x, y))
        if hasattr(child, "parent"):
            child.parent = self

    def set_size_request(self, width: int, height: int) -> None:
        self.size_request = (width, height)

    def show_all(self) -> None:
        return

    def get_preferred_size(self):
        width, height = self.size_request
        return (
            SimpleNamespace(width=width, height=height),
            SimpleNamespace(width=width, height=height),
        )


class FakeDrawingArea:
    def __init__(self) -> None:
        self.size_request = (1, 1)
        self.events = 0
        self._signals: dict[str, list[tuple[object, tuple[object, ...]]]] = {}
        self.parent = None
        self.shown = False
        self.draw_queued = False

    def set_size_request(self, width: int, height: int) -> None:
        self.size_request = (width, height)

    def add_events(self, events: int) -> None:
        self.events = events

    def connect(self, signal: str, callback, *args) -> None:
        self._signals.setdefault(signal, []).append((callback, args))

    def show_all(self) -> None:
        self.shown = True

    def queue_draw(self) -> None:
        self.draw_queued = True

    def get_preferred_size(self):
        width, height = self.size_request
        return (
            SimpleNamespace(width=width, height=height),
            SimpleNamespace(width=width, height=height),
        )


class FakePixbuf:
    def __init__(self, width: int, height: int, scaled=None) -> None:
        self.width = width
        self.height = height
        self.scaled = scaled
        self.scale_calls: list[tuple[int, int, object]] = []

    def get_width(self) -> int:
        return self.width

    def get_height(self) -> int:
        return self.height

    def scale_simple(self, width: int, height: int, interp):
        self.scale_calls.append((width, height, interp))
        return self.scaled


class FakeMonitor:
    def __init__(self) -> None:
        self.changed = None
        self.cancelled = False

    def connect(self, signal: str, callback, *args) -> None:
        self.changed = (signal, callback, args)

    def cancel(self) -> None:
        self.cancelled = True


class FakeWindowType:
    POPUP = 0


class FakeWindowTypeHint:
    TOOLTIP = 0


class FakeRevealerTransitionType:
    SLIDE_UP = 0
    SLIDE_DOWN = 1
    SLIDE_RIGHT = 2
    SLIDE_LEFT = 3


class FakeAlign:
    FILL = 0
    CENTER = 1


class FakePolicyType:
    NEVER = 0
    AUTOMATIC = 1


class FakeReliefStyle:
    NONE = 0


class FakePositionType:
    TOP = 0
    BOTTOM = 1
    LEFT = 2
    RIGHT = 3


class FakeOrientation:
    HORIZONTAL = 0
    VERTICAL = 1


class FakeGtk:
    Menu = FakeMenu
    MenuItem = FakeMenuItem
    Window = FakeWindow
    Button = FakeButton
    CheckMenuItem = FakeCheckMenuItem
    CheckButton = FakeCheckButton
    RadioMenuItem = FakeRadioMenuItem
    SeparatorMenuItem = FakeSeparatorMenuItem
    Separator = FakeSeparator
    ScrolledWindow = FakeScrolledWindow
    ComboBoxText = FakeComboBoxText
    Image = FakeImage
    Box = FakeBox
    Label = FakeLabel
    Fixed = FakeFixed
    DrawingArea = FakeDrawingArea
    Revealer = FakeRevealer
    Orientation = FakeOrientation
    Align = FakeAlign
    PolicyType = FakePolicyType
    ReliefStyle = FakeReliefStyle
    PositionType = FakePositionType
    WindowType = FakeWindowType
    WindowTypeHint = FakeWindowTypeHint
    RevealerTransitionType = FakeRevealerTransitionType
    main_quit = MagicMock()


def _labels(menu: FakeMenu) -> list[str]:
    return [child.get_label() for child in menu.get_children()]


def _frame(*, item=None, item_index: int = -1, insert_index: int = 0):
    item_geometries = []
    if item is not None:
        item_geometries.append(
            SimpleNamespace(item=item, draw_rect=SimpleNamespace(x=0, y=0, w=48, h=48))
        )
    return SimpleNamespace(
        item_at_point=MagicMock(return_value=item),
        item_index_at_point=MagicMock(return_value=item_index),
        insertion_index_for_main=MagicMock(return_value=insert_index),
        item_geometries=item_geometries,
    )


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setattr(menu_mod, "Gtk", FakeGtk)
    monkeypatch.setattr(menu_mod, "Pango", FakePango)
    monkeypatch.setattr(folder_stack_mod, "Gtk", FakeGtk)
    monkeypatch.setattr(folder_stack_mod, "Pango", FakePango)
    monkeypatch.setattr(folder_stack_mod, "PangoCairo", FakePangoCairo)
    monkeypatch.setattr(stack_mod, "Gtk", FakeGtk)
    monkeypatch.setattr(stack_mod, "Pango", FakePango)
    monkeypatch.setattr(stack_mod, "PangoCairo", FakePangoCairo)
    monkeypatch.setattr(menu_mod, "load_catalog_icon", lambda applet_id, size: None)
    about = MagicMock()
    settings = MagicMock()
    runtime = MagicMock()
    runtime.cursor_position.return_value = (20.0, 8.0)

    model = MagicMock()
    model.pinned_items = []
    config = SimpleNamespace(
        lock_icons=False,
        hide_mode="autohide",
        previews_enabled=True,
        tooltips_enabled=True,
        monitor_index=-1,
        active_display=False,
        current_workspace_only=False,
        anchor_applets=False,
        anchor_files=False,
        theme="default",
        icon_size=48,
        pos="bottom",
        position="bottom",
        item_prefs={},
        window_list_sort="default",
        save=MagicMock(),
        show_recent_docs_in_menu=False,
        recent_docs_max=10,
        show_recent_apps=False,
        recent_apps_max=5,
        recent_apps_retention_days=14,
        recent_apps=[],
        recent_apps_opacity=0.85,
    )
    tracker = MagicMock()
    tracker.list_windows.return_value = []
    preview_service = MagicMock()
    preview_service.thumbnail.return_value = None
    launcher = MagicMock()
    launcher.default_directory_app_name.return_value = None
    folder_stack = folder_stack_mod.FolderStackController(
        config=config,
        runtime=runtime,
        launcher=launcher,
    )
    return menu_mod.MenuHandler(
        about=about,
        settings=settings,
        runtime=runtime,
        model=model,
        config=config,
        window_tracker=tracker,
        preview_service=preview_service,
        folder_stack=folder_stack,
        diagnostics=MagicMock(),
        launcher=launcher,
        dock_window=MagicMock(),
    )


class TestItemMenus:
    def test_regular_running_item_menu_actions(self, handler, monkeypatch):
        # Given
        menu = FakeMenu()
        item = DockItem(
            desktop_id="firefox.desktop",
            is_pinned=True,
            is_running=True,
            instance_count=2,
        )

        monkeypatch.setattr(
            handler,
            "_append_desktop_actions",
            lambda menu, desktop_id: menu.append(FakeMenuItem(label="Desktop Action")),
        )
        # When
        handler._build_item_menu(menu=menu, item=item)
        labels = _labels(menu)
        # Then
        assert "Desktop Action" in labels
        assert "Remove from Dock" in labels
        assert "Close All" in labels

        next(
            mi for mi in menu.children if mi.get_label() == "Remove from Dock"
        ).activate()
        handler._model.unpin_item.assert_called_once_with("firefox.desktop")

        next(mi for mi in menu.children if mi.get_label() == "Close All").activate()
        handler._tracker.close_all.assert_called_once_with("firefox.desktop")

    def test_pinned_app_item_menu_includes_icon_submenu(self, handler):
        menu = FakeMenu()
        item = DockItem(desktop_id="firefox.desktop", is_pinned=True)

        handler._build_item_menu(menu=menu, item=item)

        icon_menu = next(mi for mi in menu.children if mi.get_label() == "Icon")
        assert [mi.get_label() for mi in icon_menu.get_submenu().get_children()] == [
            "Default Icon",
            "Choose From File...",
            "Reset Custom Icon",
        ]

    def test_unpinned_app_item_menu_does_not_expose_icon_editing(self, handler):
        menu = FakeMenu()
        item = DockItem(desktop_id="firefox.desktop", is_pinned=False)

        handler._build_item_menu(menu=menu, item=item)

        assert "Icon" not in _labels(menu)

    def test_locked_icons_still_allow_pinned_icon_submenu(self, handler):
        handler._config.lock_icons = True
        menu = FakeMenu()
        item = DockItem(desktop_id="firefox.desktop", is_pinned=True)

        handler._build_item_menu(menu=menu, item=item)

        labels = _labels(menu)
        assert "Icon" in labels
        assert "Remove from Dock" not in labels

    def test_choose_custom_icon_calls_model_api(self, handler, monkeypatch, tmp_path):
        menu = FakeMenu()
        item = DockItem(desktop_id="firefox.desktop", is_pinned=True)
        selected = tmp_path / "icon.png"
        monkeypatch.setattr(handler, "_choose_icon_file", lambda: selected)

        handler._build_item_menu(menu=menu, item=item)
        icon_menu = next(mi for mi in menu.children if mi.get_label() == "Icon")
        choose = next(
            mi
            for mi in icon_menu.get_submenu().get_children()
            if mi.get_label() == "Choose From File..."
        )
        choose.activate()

        handler._model.set_custom_icon.assert_called_once_with(item=item, path=selected)

    def test_reset_custom_icon_calls_model_api(self, handler):
        menu = FakeMenu()
        item = DockItem(desktop_id="firefox.desktop", is_pinned=True)

        handler._build_item_menu(menu=menu, item=item)
        icon_menu = next(mi for mi in menu.children if mi.get_label() == "Icon")
        reset = next(
            mi
            for mi in icon_menu.get_submenu().get_children()
            if mi.get_label() == "Reset Custom Icon"
        )
        reset.activate()

        handler._model.reset_custom_icon.assert_called_once_with(item)

    def test_applet_item_menu_includes_icon_source_when_supported(self, handler):
        # Given
        menu = FakeMenu()
        applet_item = DockItem(desktop_id="applet://session")
        applet = SimpleNamespace(
            icon_source_options=(
                menu_mod.IconSource.DOCKING,
                menu_mod.IconSource.SYSTEM,
            ),
            get_menu_items=MagicMock(return_value=[FakeMenuItem(label="Lock Screen")]),
            icon_source=MagicMock(return_value=menu_mod.ICON_SOURCE_DOCKING),
            set_icon_source=MagicMock(),
        )
        handler._model.get_applet.return_value = applet

        # When
        handler._build_item_menu(menu=menu, item=applet_item)
        labels = _labels(menu)
        icon_menu = next(mi for mi in menu.children if mi.get_label() == "Icon")
        icon_options = icon_menu.get_submenu().get_children()
        system_option = next(
            mi for mi in icon_options if mi.get_label() == "System Icon"
        )
        system_option.set_active(True)
        system_option.activate()

        # Then
        assert labels == [
            "Lock Screen",
            "---",
            "Icon",
            "---",
            "Remove from Dock",
        ]
        assert [mi.get_label() for mi in icon_options] == [
            "Docking Icon",
            "System Icon",
        ]
        applet.get_menu_items.assert_called_once_with()
        applet.set_icon_source.assert_called_once_with(menu_mod.ICON_SOURCE_SYSTEM)

    def test_applet_item_menu_includes_applet_items_and_remove(self, handler):
        # Given
        menu = FakeMenu()
        applet_item = DockItem(desktop_id="applet://quote")
        applet = MagicMock()
        applet.get_menu_items.return_value = [FakeMenuItem(label="Refresh Quote")]
        handler._model.get_applet.return_value = applet

        # When
        handler._build_item_menu(menu=menu, item=applet_item)
        labels = _labels(menu)
        # Then
        assert "Refresh Quote" in labels
        assert "Remove from Dock" in labels

        next(
            mi for mi in menu.children if mi.get_label() == "Remove from Dock"
        ).activate()
        handler._model.remove_applet.assert_called_once_with("applet://quote")

    def test_applet_item_menu_hides_remove_when_locked(self, handler):
        # Given
        handler._config.lock_icons = True
        menu = FakeMenu()
        applet_item = DockItem(desktop_id="applet://quote")
        applet = MagicMock()
        applet.get_menu_items.return_value = [FakeMenuItem(label="Refresh")]
        handler._model.get_applet.return_value = applet

        # When
        handler._build_item_menu(menu=menu, item=applet_item)
        # Then
        assert "Remove from Dock" not in _labels(menu)

    def test_folder_item_menu_exposes_view_options(self, handler, monkeypatch):
        menu = FakeMenu()
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
            name="docs",
            is_pinned=True,
        )
        monkeypatch.setattr(handler, "_populate_directory_menu", lambda **kwargs: None)

        handler._build_item_menu(menu=menu, item=item)

        labels = _labels(menu)
        assert "Sort By" in labels
        assert "Show Hidden Files" in labels
        assert "Large Icons" not in labels

    def test_folder_item_menu_keeps_all_entries_and_actions_in_one_menu(
        self, handler, monkeypatch
    ):
        menu = FakeMenu()
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
            name="docs",
            is_pinned=True,
        )
        monkeypatch.setattr(
            handler._folder_stack,
            "list_directory",
            lambda **kwargs: [
                {
                    "target": f"file:///tmp/docs/{i}",
                    "name": f"Item {i}",
                    "is_dir": False,
                    "icon": None,
                }
                for i in range(23)
            ],
        )
        handler._build_item_menu(menu=menu, item=item)

        labels = _labels(menu)
        assert "Item 0" in labels
        assert "Item 22" in labels
        assert "Sort By" in labels
        assert "Show Hidden Files" in labels
        assert "Large Icons" not in labels
        assert not any(label.startswith("More (") for label in labels)

    def test_directory_rows_ellipsize_long_names(self, handler, monkeypatch):
        menu = FakeMenu()
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
            name="docs",
            is_pinned=True,
        )
        monkeypatch.setattr(
            handler._folder_stack,
            "list_directory",
            lambda **kwargs: [
                {
                    "target": "file:///tmp/docs/very-long-name",
                    "name": "this-is-a-very-long-filename-that-should-not-stretch-the-menu",
                    "is_dir": False,
                    "icon": None,
                }
            ],
        )

        handler._build_item_menu(menu=menu, item=item)

        row = menu.children[0]
        assert isinstance(row.get_child(), FakeBox)
        text = row.get_child().children[0]
        assert isinstance(text, FakeLabel)
        assert text.max_width_chars == menu_mod.MENU_LABEL_MAX_CHARS
        assert text.single_line_mode is True

    def test_empty_nested_directory_has_no_submenu_arrow(self, handler, monkeypatch):
        menu = FakeMenu()
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
            name="docs",
            is_pinned=True,
        )
        opened: list[str] = []
        monkeypatch.setattr(
            menu_mod.launcher_mod, "open_target", lambda target: opened.append(target)
        )

        handler._append_directory_row(
            menu=menu,
            folder_item=item,
            child={
                "target": "file:///tmp/docs/empty",
                "name": "empty",
                "is_dir": True,
                "has_children": False,
                "icon": None,
            },
        )

        row = menu.children[0]
        assert row.get_submenu() is None

        row.activate()
        assert opened == ["file:///tmp/docs/empty"]

    def test_directory_rows_prefer_system_gicon(self, handler, monkeypatch):
        gicon = MagicMock()
        info = MagicMock()
        info.get_name.return_value = "docs"
        info.get_display_name.return_value = "docs"
        info.get_icon.return_value = gicon
        info.get_content_type.return_value = "inode/directory"
        info.get_file_type.return_value = menu_mod.Gio.FileType.DIRECTORY
        info.get_is_hidden.return_value = False
        info.get_size.return_value = 0
        info.get_attribute_uint64.return_value = 0
        enumerator = MagicMock()
        enumerator.next_file.side_effect = [info, None]
        folder = MagicMock()
        folder.enumerate_children.return_value = enumerator
        child = MagicMock()
        child.get_uri.return_value = "file:///tmp/docs"
        folder.get_child.return_value = child
        monkeypatch.setattr(menu_mod.Gio.File, "new_for_uri", lambda _uri: folder)
        monkeypatch.setattr(
            handler._folder_stack._browser,
            "directory_has_visible_children",
            lambda **_kwargs: False,
        )
        handler._launcher.resolve_file_icon.return_value = "folder-pixbuf"
        item = DockItem(
            desktop_id="file:///tmp/root",
            kind=FOLDER_KIND,
            target="file:///tmp/root",
            prefs_key="file:///tmp/root",
        )

        rows = handler._folder_stack.list_directory(
            folder_item=item, target="file:///tmp/root"
        )

        assert rows[0]["icon"] == "folder-pixbuf"
        handler._launcher.resolve_file_icon.assert_called_once_with(
            target="file:///tmp/docs",
            gicon=gicon,
            content_type="inode/directory",
            size=16,
            is_dir=True,
        )

    def test_list_directory_reuses_cached_rows_for_same_folder(
        self, handler, monkeypatch
    ):
        gicon = MagicMock()
        info = MagicMock()
        info.get_name.return_value = "docs"
        info.get_display_name.return_value = "docs"
        info.get_icon.return_value = gicon
        info.get_content_type.return_value = "inode/directory"
        info.get_file_type.return_value = menu_mod.Gio.FileType.DIRECTORY
        info.get_is_hidden.return_value = False
        info.get_size.return_value = 0
        info.get_attribute_uint64.return_value = 0
        enumerator = MagicMock()
        enumerator.next_file.side_effect = [info, None]
        folder = MagicMock()
        folder.enumerate_children.return_value = enumerator
        child = MagicMock()
        child.get_uri.return_value = "file:///tmp/docs"
        folder.get_child.return_value = child
        monkeypatch.setattr(menu_mod.Gio.File, "new_for_uri", lambda _uri: folder)
        monkeypatch.setattr(
            handler._folder_stack._browser,
            "directory_has_visible_children",
            lambda **_kwargs: False,
        )
        handler._launcher.resolve_file_icon.return_value = "folder-pixbuf"
        item = DockItem(
            desktop_id="file:///tmp/root",
            kind=FOLDER_KIND,
            target="file:///tmp/root",
            prefs_key="file:///tmp/root",
        )

        first = handler._folder_stack.list_directory(
            folder_item=item, target="file:///tmp/root"
        )
        second = handler._folder_stack.list_directory(
            folder_item=item, target="file:///tmp/root"
        )

        assert first == second
        folder.enumerate_children.assert_called_once()
        handler._launcher.resolve_file_icon.assert_called_once()

    def test_file_item_menu_opens_target(self, handler, monkeypatch):
        menu = FakeMenu()
        item = DockItem(
            desktop_id="file:///tmp/notes.txt",
            kind=FILE_KIND,
            target="file:///tmp/notes.txt",
            name="notes.txt",
            is_pinned=True,
        )
        opened: list[str] = []
        monkeypatch.setattr(
            menu_mod.launcher_mod, "open_target", lambda target: opened.append(target)
        )

        handler._build_item_menu(menu=menu, item=item)

        next(mi for mi in menu.children if mi.get_label() == "Open").activate()
        assert opened == ["file:///tmp/notes.txt"]

    def test_running_app_menu_shows_window_row_with_inline_close(
        self, handler, monkeypatch
    ):
        menu = FakeMenu()
        item = DockItem(
            desktop_id="firefox.desktop",
            is_running=True,
            instance_count=1,
        )
        window_id = WindowId.x11(7)
        pixbuf = object()
        handler._tracker.list_windows.return_value = [
            WindowSnapshot(
                id=window_id,
                desktop_id="firefox.desktop",
                title="A" * 80,
            )
        ]
        handler._preview_service.thumbnail.return_value = PreviewImage(
            image=pixbuf,
            width=menu_mod.WINDOW_MENU_THUMB_W,
            height=menu_mod.WINDOW_MENU_THUMB_H,
        )
        monkeypatch.setattr(menu_mod.launcher_mod, "get_actions", lambda **_kwargs: [])

        handler._build_item_menu(menu=menu, item=item)

        row = menu.children[0]
        assert isinstance(row.get_child(), FakeBox)
        assert isinstance(row.get_child().children[0], FakeImage)
        assert row.get_child().children[0].pixbuf is pixbuf
        handler._preview_service.thumbnail.assert_called_once_with(
            window_id,
            width=menu_mod.WINDOW_MENU_THUMB_W,
            height=menu_mod.WINDOW_MENU_THUMB_H,
        )
        assert isinstance(row.get_child().children[1], FakeLabel)
        assert (
            row.get_child().children[1].max_width_chars == menu_mod.MENU_LABEL_MAX_CHARS
        )
        close_label = row.get_child().children[2]
        assert isinstance(close_label, FakeLabel)
        assert close_label.label == "×"

        monkeypatch.setattr(
            menu_mod.GLib,
            "idle_add",
            lambda *_args: pytest.fail("X11 row removal should not be deferred"),
        )
        close_event = SimpleNamespace(x=170.0)
        assert row.emit("button-press-event", close_event) is True
        assert row.emit("button-release-event", close_event) is True
        handler._tracker.close.assert_called_once_with(window_id)
        handler._runtime.hide_hover_ui.assert_called_once()
        assert row.hidden is True
        assert row.destroyed is True
        assert row not in menu.children
        assert not any(
            getattr(child, "_window_rows_separator", False) for child in menu.children
        )
        assert menu.popdown_called is True
        assert menu.shown is True
        assert menu.resize_queued is True
        assert menu.resize_checked is True
        assert menu.draw_queued is True
        assert menu.popup_event is close_event

        row.activate()
        handler._tracker.activate.assert_called_once_with(window_id)

    def test_wayland_window_row_close_defers_menu_mutation(self, handler, monkeypatch):
        menu = FakeMenu()
        item = DockItem(
            desktop_id="firefox.desktop",
            is_running=True,
            instance_count=1,
        )
        window_id = WindowId(DisplayServer.WAYLAND, 7)
        handler._tracker.list_windows.return_value = [
            WindowSnapshot(
                id=window_id,
                desktop_id="firefox.desktop",
                title="Firefox",
            )
        ]
        handler._preview_service.thumbnail.return_value = None
        monkeypatch.setattr(menu_mod.launcher_mod, "get_actions", lambda **_kwargs: [])
        idle_calls: list[tuple[object, tuple[object, ...]]] = []
        monkeypatch.setattr(
            menu_mod.GLib,
            "idle_add",
            lambda callback, *args: idle_calls.append((callback, args)) or 91,
        )

        handler._build_item_menu(menu=menu, item=item)
        row = menu.children[0]
        close_event = SimpleNamespace(x=170.0)

        assert row.emit("button-release-event", close_event) is True

        handler._tracker.close.assert_called_once_with(window_id)
        handler._runtime.hide_hover_ui.assert_called_once()
        assert row in menu.children
        assert row.hidden is False
        assert idle_calls == [(handler._remove_window_row_deferred, (row, close_event))]

        assert idle_calls[0][0](*idle_calls[0][1]) is False
        assert row.hidden is True
        assert row.destroyed is True
        assert row not in menu.children
        assert menu.popdown_called is False
        assert menu.popup_event is None
        assert menu.shown is True
        assert menu.resize_queued is True
        assert menu.resize_checked is True
        assert menu.draw_queued is True

    def test_window_list_default_preserves_tracker_order(self, handler, monkeypatch):
        menu = FakeMenu()
        item = DockItem(
            desktop_id="code.desktop",
            is_running=True,
            instance_count=3,
        )
        handler._tracker.list_windows.return_value = [
            WindowSnapshot(
                id=WindowId.x11(1), desktop_id="code.desktop", title="Charlie"
            ),
            WindowSnapshot(
                id=WindowId.x11(2), desktop_id="code.desktop", title="Alpha"
            ),
            WindowSnapshot(
                id=WindowId.x11(3), desktop_id="code.desktop", title="Bravo"
            ),
        ]
        handler._config.window_list_sort = "default"
        monkeypatch.setattr(menu_mod.launcher_mod, "get_actions", lambda **_kwargs: [])

        handler._build_item_menu(menu=menu, item=item)

        rows = [c for c in menu.children if getattr(c, "_window_row", False)]
        labels = [r.get_child().children[1].label for r in rows]
        assert labels == ["Charlie", "Alpha", "Bravo"]

    def test_window_list_alphabetical_sorts_by_title(self, handler, monkeypatch):
        menu = FakeMenu()
        item = DockItem(
            desktop_id="code.desktop",
            is_running=True,
            instance_count=3,
        )
        handler._tracker.list_windows.return_value = [
            WindowSnapshot(
                id=WindowId.x11(1), desktop_id="code.desktop", title="Charlie"
            ),
            WindowSnapshot(
                id=WindowId.x11(2), desktop_id="code.desktop", title="Alpha"
            ),
            WindowSnapshot(
                id=WindowId.x11(3), desktop_id="code.desktop", title="Bravo"
            ),
        ]
        handler._config.window_list_sort = "alphabetical"
        monkeypatch.setattr(menu_mod.launcher_mod, "get_actions", lambda **_kwargs: [])

        handler._build_item_menu(menu=menu, item=item)

        rows = [c for c in menu.children if getattr(c, "_window_row", False)]
        labels = [r.get_child().children[1].label for r in rows]
        assert labels == ["Alpha", "Bravo", "Charlie"]


class TestDockMenu:
    def test_build_dock_menu_wires_separator_quit_and_applets(
        self, handler, monkeypatch
    ):
        # Given
        menu = FakeMenu()
        handler._runtime.quit.reset_mock()
        handler._model.pinned_items = [DockItem(desktop_id="applet://clock")]
        monkeypatch.setattr(
            menu_mod,
            "get_applet_catalog",
            lambda: {
                "clock": _catalog_entry(
                    applet_id="clock",
                    name="Clock",
                    category=menu_mod.AppletCategory.PRODUCTIVITY,
                ),
                "calendar": _catalog_entry(
                    applet_id="calendar",
                    name="Calendar",
                    category=menu_mod.AppletCategory.PRODUCTIVITY,
                ),
                "separator": _catalog_entry(applet_id="separator", name="Separator"),
            },
        )

        # When
        handler._build_dock_menu(menu=menu, insert_index=3)
        labels = _labels(menu)
        # Then
        assert menu_mod._("Add Applet") in labels
        assert "Add Separator" in labels
        assert "Diagnostics" in labels
        assert "Preferences" in labels
        assert "About" in labels
        assert "Get Support" in labels
        assert "Quit" in labels
        assert "Auto-hide" not in labels
        assert "Window Previews" not in labels
        assert "Icons" not in labels
        assert "Themes" not in labels
        assert "Position" not in labels
        assert labels.index("Preferences") == labels.index("About") - 1
        assert labels.index("About") == labels.index("Get Support") - 1
        assert labels.index("Get Support") == labels.index("Quit") - 1

        next(mi for mi in menu.children if mi.get_label() == "Add Separator").activate()
        handler._model.add_separator.assert_called_once_with(index=3)

        show_about = MagicMock()
        handler._about.show = show_about
        show_diagnostics = MagicMock()
        handler._diagnostics.show = show_diagnostics
        show_settings = MagicMock()
        handler._settings.show = show_settings
        open_target = MagicMock()
        menu_mod.launcher_mod.open_target = open_target
        next(mi for mi in menu.children if mi.get_label() == "Diagnostics").activate()
        show_diagnostics.assert_called_once()

        next(mi for mi in menu.children if mi.get_label() == "Preferences").activate()
        show_settings.assert_called_once()

        next(mi for mi in menu.children if mi.get_label() == "About").activate()
        show_about.assert_called_once()

        next(mi for mi in menu.children if mi.get_label() == "Get Support").activate()
        open_target.assert_called_once_with(menu_mod.SUPPORT_URL)

        next(mi for mi in menu.children if mi.get_label() == "Quit").activate()
        handler._runtime.quit.assert_called_once()

        applets_item = next(
            mi for mi in menu.children if mi.get_label() == menu_mod._("Add Applet")
        )
        submenu_labels = _labels(applets_item.get_submenu())
        assert submenu_labels == ["Time & Productivity"]
        category_item = applets_item.get_submenu().get_children()[0]
        item = next(
            mi
            for mi in category_item.get_submenu().get_children()
            if mi.get_label() == "Calendar"
        )
        item.activate()
        handler._model.add_applet.assert_called_once_with("calendar")

    def test_build_dock_menu_groups_available_applets_into_ordered_submenus(
        self, handler, monkeypatch
    ):
        menu = FakeMenu()
        handler._model.pinned_items = [DockItem(desktop_id="applet://clock")]
        monkeypatch.setattr(menu_mod, "load_catalog_icon", lambda applet_id, size: None)
        monkeypatch.setattr(
            menu_mod,
            "get_applet_catalog",
            lambda: {
                "devices": _catalog_entry(
                    applet_id="devices",
                    name="Devices",
                    category=menu_mod.AppletCategory.SYSTEM,
                ),
                "calendar": _catalog_entry(
                    applet_id="calendar",
                    name="Calendar",
                    category=menu_mod.AppletCategory.PRODUCTIVITY,
                ),
                "applications": _catalog_entry(
                    applet_id="applications",
                    name="Applications",
                    category=menu_mod.AppletCategory.LAUNCHER,
                ),
                "alarm": _catalog_entry(
                    applet_id="alarm",
                    name="Alarm",
                    category=menu_mod.AppletCategory.PRODUCTIVITY,
                ),
                "clock": _catalog_entry(
                    applet_id="clock",
                    name="Clock",
                    category=menu_mod.AppletCategory.PRODUCTIVITY,
                ),
                "separator": _catalog_entry(
                    applet_id="separator",
                    name="Separator",
                    category=menu_mod.AppletCategory.OTHER,
                ),
            },
        )

        handler._build_dock_menu(menu=menu, insert_index=0)

        add_applet = next(
            item
            for item in menu.children
            if item.get_label() == menu_mod._("Add Applet")
        )
        category_items = add_applet.get_submenu().get_children()
        assert _labels(add_applet.get_submenu()) == [
            "Launcher & Navigation",
            "Time & Productivity",
            "System & Power",
        ]
        assert _labels(category_items[0].get_submenu()) == ["Applications"]
        assert _labels(category_items[1].get_submenu()) == ["Alarm", "Calendar"]
        assert _labels(category_items[2].get_submenu()) == ["Devices"]

    def test_show_builds_background_menu_and_pops_at_pointer(
        self, handler, monkeypatch
    ):
        # Given
        event = SimpleNamespace(x=10.0, y=5.0)
        frame = _frame(item=None, insert_index=1)
        captured_menu = None

        class CaptureMenu(FakeMenu):
            def __init__(self):
                nonlocal captured_menu
                super().__init__()
                captured_menu = self

        monkeypatch.setattr(
            menu_mod,
            "Gtk",
            SimpleNamespace(
                Menu=CaptureMenu,
                MenuItem=FakeMenuItem,
                CheckMenuItem=FakeCheckMenuItem,
                RadioMenuItem=FakeRadioMenuItem,
                SeparatorMenuItem=FakeSeparatorMenuItem,
                main_quit=FakeGtk.main_quit,
            ),
        )
        monkeypatch.setattr(
            handler, "_build_dock_menu", lambda menu, insert_index: None
        )

        # When
        handler.show(event=event, cursor_main=10.0, frame=frame)
        # Then
        assert captured_menu is not None
        assert captured_menu.shown is True
        assert captured_menu.popup_event is event
        handler._runtime.menu_popup_opened.assert_called_once()
        captured_menu.emit("deactivate")
        handler._runtime.menu_popup_closed.assert_called_once()

    def test_build_dock_menu_shows_empty_add_applet_submenu_when_all_active(
        self, handler
    ):
        menu = FakeMenu()
        handler._model.pinned_items = [DockItem(desktop_id="applet://clock")]
        handler._model.get_applet.return_value = None
        with patch.object(
            menu_mod,
            "get_applet_catalog",
            return_value={"clock": _catalog_entry(applet_id="clock", name="Clock")},
        ):
            handler._build_dock_menu(menu=menu, insert_index=0)

        add_applet = next(
            mi for mi in menu.children if mi.get_label() == menu_mod._("Add Applet")
        )
        submenu_labels = _labels(add_applet.get_submenu())
        assert submenu_labels == [menu_mod._("No Applets Available")]

    def test_build_dock_menu_uses_catalog_without_importing_applet_modules(
        self, handler, monkeypatch
    ):
        import docking.applets as applets_mod

        menu = FakeMenu()
        handler._model.pinned_items = []
        monkeypatch.setattr(menu_mod, "load_catalog_icon", lambda applet_id, size: None)

        with patch.object(
            applets_mod,
            "import_module",
            side_effect=AssertionError("unexpected import"),
        ):
            handler._build_dock_menu(menu=menu, insert_index=0)

        assert any(
            item.get_label() == menu_mod._("Add Applet") for item in menu.children
        )


class TestMenuCallbacks:
    def test_show_builds_item_menu(self, handler, monkeypatch):
        event = SimpleNamespace(x=20.0, y=9.0)
        item = DockItem(desktop_id="firefox.desktop")
        frame = _frame(item=item)
        built: list[tuple[str, object]] = []

        def capture_build(*, menu, item):
            built.append(("item", item))

        monkeypatch.setattr(handler, "_build_item_menu", capture_build)

        handler.show(event=event, cursor_main=20.0, frame=frame)

        assert built == [("item", item)]
        assert handler._runtime.menu_popup_opened.call_count == 1

    def test_show_can_force_background_menu_over_item(self, handler, monkeypatch):
        event = SimpleNamespace(x=20.0, y=9.0)
        item = DockItem(desktop_id="firefox.desktop")
        frame = _frame(item=item, insert_index=2)
        built: list[tuple[str, object]] = []

        monkeypatch.setattr(
            handler,
            "_build_item_menu",
            lambda *, menu, item: built.append(("item", item)),
        )
        monkeypatch.setattr(
            handler,
            "_build_dock_menu",
            lambda *, menu, insert_index: built.append(("dock", insert_index)),
        )

        handler.show(
            event=event,
            cursor_main=20.0,
            frame=frame,
            force_background=True,
        )

        assert built == [("dock", 2)]
        assert handler._runtime.menu_popup_opened.call_count == 1

    def test_append_desktop_actions_triggers_launch_action(self, handler, monkeypatch):
        # Given
        menu = FakeMenu()
        launch_calls: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "docking.platform.launcher.get_actions",
            lambda desktop_id: [("new-window", "New Window")],
        )
        monkeypatch.setattr(
            "docking.platform.launcher.launch_action",
            lambda desktop_id, action_id: launch_calls.append((desktop_id, action_id)),
        )

        handler._append_desktop_actions(menu=menu, desktop_id="firefox.desktop")
        # When
        next(mi for mi in menu.children if mi.get_label() == "New Window").activate()
        # Then
        assert launch_calls == [("firefox.desktop", "new-window")]

    def test_insert_index(self, handler):
        frame = _frame(item_index=0, insert_index=1)

        idx = handler._insert_index(cursor_main=40, frame=frame)
        assert idx == 1

    def test_folder_pref_callbacks_persist(self, handler):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
            prefs_key="file:///tmp/docs",
        )
        toggle = FakeCheckMenuItem("Show Hidden Files")
        toggle.set_active(True)

        handler._on_folder_hidden_toggled(toggle, item)
        handler._folder_stack.update_folder_pref(item, "unsupported", True)

        assert handler._config.item_prefs["file:///tmp/docs"]["show_hidden"] is True
        assert "unsupported" not in handler._config.item_prefs["file:///tmp/docs"]
        assert handler._config.save.call_count >= 1

    def test_folder_menu_change_debounces_refresh(self, handler, monkeypatch):
        menu = FakeMenu()
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        removed: list[int] = []
        timeout_calls: list[tuple[int, object, tuple[object, ...]]] = []
        monkeypatch.setattr(
            menu_mod.GLib, "source_remove", lambda source: removed.append(source)
        )
        monkeypatch.setattr(
            menu_mod.GLib,
            "timeout_add",
            lambda delay, cb, *args: timeout_calls.append((delay, cb, args)) or 99,
        )
        handler._folder_menu_context[id(menu)] = (menu, item, item.target, False)
        handler._folder_menu_refresh_sources[id(menu)] = 12

        handler._on_folder_menu_changed(
            MagicMock(), MagicMock(), None, MagicMock(), id(menu)
        )

        assert removed == [12]
        assert timeout_calls[0][0] == 120
        assert handler._folder_menu_refresh_sources[id(menu)] == 99

    def test_refresh_folder_menu_rebuilds_submenu(self, handler, monkeypatch):
        menu = FakeMenu()
        menu.append(FakeMenuItem(label="stale"))
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        called: list[str] = []
        monkeypatch.setattr(
            handler,
            "_populate_directory_menu",
            lambda **kwargs: called.append(kwargs["target"]),
        )
        handler._folder_menu_context[id(menu)] = (menu, item, item.target, False)

        result = handler._refresh_folder_menu(id(menu))

        assert result is False
        assert called == ["file:///tmp/docs"]
        assert menu.shown is True

    def test_folder_stack_cards_reuse_cached_layout(self, handler, monkeypatch):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        calls: list[str] = []
        monkeypatch.setattr(
            handler._folder_stack._browser, "target_state", lambda _target: "ok"
        )
        monkeypatch.setattr(
            handler._folder_stack,
            "_list_directory_rows",
            lambda **_kwargs: (
                calls.append("listed")
                or [
                    {
                        "target": "file:///tmp/docs/readme.txt",
                        "name": "readme.txt",
                        "is_dir": False,
                        "icon": object(),
                    }
                ]
            ),
        )

        first = handler._folder_stack._folder_stack_cards_for_item(item)
        second = handler._folder_stack._folder_stack_cards_for_item(item)

        assert first == second
        assert calls == ["listed"]

    def test_folder_stack_requests_dock_sized_icons(self, handler, monkeypatch):
        handler._config.icon_size = 52
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        requested_icon_sizes: list[int] = []

        monkeypatch.setattr(
            handler._folder_stack._browser, "target_state", lambda _target: "ok"
        )
        monkeypatch.setattr(
            handler._folder_stack,
            "_list_directory_rows",
            lambda **kwargs: (
                requested_icon_sizes.append(kwargs["icon_px"])
                or [
                    {
                        "target": "file:///tmp/docs/readme.txt",
                        "name": "readme.txt",
                        "is_dir": False,
                        "icon": object(),
                    }
                ]
            ),
        )

        cards, _popup_w, _popup_h = handler._folder_stack._folder_stack_cards_for_item(
            item
        )

        assert requested_icon_sizes == [52]
        assert cards[1].icon_size == 52
        assert cards[1].label_w <= folder_stack_mod.FOLDER_STACK_LABEL_MAX_WIDTH_PX

    def test_folder_stack_action_chip_allows_wider_more_label(
        self, handler, monkeypatch
    ):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        monkeypatch.setattr(
            handler._folder_stack._browser, "target_state", lambda _target: "ok"
        )
        handler._launcher.default_directory_app_name.return_value = "Caja"
        monkeypatch.setattr(
            handler._folder_stack,
            "_list_directory_rows",
            lambda **_kwargs: [
                {
                    "target": f"file:///tmp/docs/{i}.txt",
                    "name": f"Item {i}",
                    "is_dir": False,
                    "icon": object(),
                }
                for i in range(14)
            ],
        )

        cards, _popup_w, _popup_h = handler._folder_stack._folder_stack_cards_for_item(
            item
        )

        assert cards[0].label == "5 More in Caja"
        expected_width = (
            folder_stack_mod._measure_stack_text_px("5 More in Caja")
            + 2 * folder_stack_mod.FOLDER_STACK_LABEL_TEXT_MARGIN_PX
            + folder_stack_mod.FOLDER_STACK_ACTION_ARROW_GAP_PX
            + folder_stack_mod.FOLDER_STACK_ACTION_ARROW_SIZE_PX
            + 10
        )
        assert cards[0].label_w == handler._folder_stack._stack_action_width(
            label="5 More in Caja"
        )
        assert cards[0].label_w == expected_width
        assert cards[0].label_w <= folder_stack_mod.FOLDER_STACK_ACTION_MAX_WIDTH_PX

    def test_folder_stack_action_chip_falls_back_without_directory_app(
        self, handler, monkeypatch
    ):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        monkeypatch.setattr(
            handler._folder_stack._browser, "target_state", lambda _target: "ok"
        )
        handler._launcher.default_directory_app_name.return_value = None
        monkeypatch.setattr(
            handler._folder_stack,
            "_list_directory_rows",
            lambda **_kwargs: [
                {
                    "target": f"file:///tmp/docs/{i}.txt",
                    "name": f"Item {i}",
                    "is_dir": False,
                    "icon": object(),
                }
                for i in range(14)
            ],
        )

        cards, _popup_w, _popup_h = handler._folder_stack._folder_stack_cards_for_item(
            item
        )

        assert cards[0].label == "5 More in Folder"

    def test_folder_stack_short_labels_fit_chip_width(self, handler, monkeypatch):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        monkeypatch.setattr(
            handler._folder_stack._browser, "target_state", lambda _target: "ok"
        )
        monkeypatch.setattr(
            handler._folder_stack,
            "_list_directory_rows",
            lambda **_kwargs: [
                {
                    "target": "file:///tmp/docs/doc",
                    "name": "doc",
                    "is_dir": True,
                    "icon": object(),
                }
            ],
        )

        cards, _popup_w, _popup_h = handler._folder_stack._folder_stack_cards_for_item(
            item
        )

        assert cards[1].label == "doc"
        assert cards[1].label_w < 50

    def test_folder_stack_arc_starts_from_first_visible_item(
        self, handler, monkeypatch
    ):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        monkeypatch.setattr(
            handler._folder_stack._browser, "target_state", lambda _target: "ok"
        )
        monkeypatch.setattr(
            handler._folder_stack,
            "_list_directory_rows",
            lambda **_kwargs: [
                {
                    "target": f"file:///tmp/docs/{i}.txt",
                    "name": f"Item {i}",
                    "is_dir": False,
                    "icon": object(),
                }
                for i in range(4)
            ],
        )

        cards, popup_w, _popup_h = handler._folder_stack._folder_stack_cards_for_item(
            item
        )

        icon_cards = [card for card in cards if card.icon_size > 0]
        assert len(icon_cards) == 4
        fold_center_x = handler._folder_stack._folder_stack_fold_center_x
        for card in icon_cards:
            icon_center_x = card.icon_x + card.icon_size / 2
            assert icon_center_x > fold_center_x
        assert icon_cards[0].icon_x > icon_cards[-1].icon_x
        assert popup_w > icon_cards[0].icon_x + icon_cards[0].icon_size

    def test_folder_stack_keeps_uniform_vertical_spacing(self, handler, monkeypatch):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        monkeypatch.setattr(
            handler._folder_stack._browser, "target_state", lambda _target: "ok"
        )
        monkeypatch.setattr(
            handler._folder_stack,
            "_list_directory_rows",
            lambda **_kwargs: [
                {
                    "target": f"file:///tmp/docs/{i}.txt",
                    "name": f"Item {i}",
                    "is_dir": False,
                    "icon": object(),
                }
                for i in range(5)
            ],
        )

        cards, _popup_w, _popup_h = handler._folder_stack._folder_stack_cards_for_item(
            item
        )

        icon_cards = [card for card in cards if card.icon_size > 0]
        centers = [card.icon_y + card.icon_size / 2 for card in icon_cards]
        gaps = [round(centers[index + 1] - centers[index], 3) for index in range(4)]
        assert len(set(gaps)) == 1

    def test_folder_stack_change_debounces_refresh(self, handler, monkeypatch):
        removed: list[int] = []
        timeout_calls: list[tuple[int, object, tuple[object, ...]]] = []
        monkeypatch.setattr(
            menu_mod.GLib, "source_remove", lambda source: removed.append(source)
        )
        monkeypatch.setattr(
            menu_mod.GLib,
            "timeout_add",
            lambda delay, cb, *args: timeout_calls.append((delay, cb, args)) or 77,
        )
        handler._folder_stack._folder_stack_refresh_source = 12

        handler._folder_stack._on_folder_stack_changed(
            MagicMock(), MagicMock(), None, MagicMock()
        )

        assert removed == [12]
        assert timeout_calls[0][0] == 120
        assert handler._folder_stack._folder_stack_refresh_source == 77

    def test_folder_stack_change_invalidates_cached_layout(self, handler, monkeypatch):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        handler._folder_stack._folder_stack_item = item
        handler._folder_stack._folder_stack_cache.layouts[
            ("file:///tmp/docs", 0, "name", False, 48, None)
        ] = folder_stack_mod.FolderStackLayout(
            cards=(),
            popup_w=1,
            popup_h=1,
            fold_center_x=1,
        )
        monkeypatch.setattr(menu_mod.GLib, "timeout_add", lambda *_args: 77)

        handler._folder_stack._on_folder_stack_changed(
            MagicMock(), MagicMock(), None, MagicMock()
        )

        assert handler._folder_stack._folder_stack_cache.layouts == {}

    def test_folder_stack_click_opens_target(self, handler):
        target = "file:///tmp/docs/readme.txt"
        handler._folder_stack._folder_stack_cards = [
            folder_stack_mod.FolderStackCard(
                label="readme.txt",
                target=target,
                icon=None,
                icon_x=80,
                icon_y=40,
                icon_size=48,
                label_x=10,
                label_y=52,
                label_w=90,
                label_h=24,
                centered=False,
            )
        ]
        opened: list[str] = []
        with patch.object(
            handler._folder_stack,
            "_open_folder_stack_target",
            opened.append,
        ):
            press = SimpleNamespace(x=32.0, y=60.0, button=1)
            release = SimpleNamespace(x=32.0, y=60.0, button=1)

            assert (
                handler._folder_stack._on_stack_button_press(FakeDrawingArea(), press)
                is True
            )
            assert (
                handler._folder_stack._on_stack_button_release(
                    FakeDrawingArea(), release
                )
                is True
            )

        assert opened == [target]

    def test_refresh_folder_stack_rebuilds_popup_content(self, handler, monkeypatch):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        window = FakeWindow()
        revealer = FakeRevealer()
        old_child = object()
        revealer.add(old_child)
        built: list[object] = []
        monkeypatch.setattr(
            handler._folder_stack,
            "_replace_folder_stack_content",
            lambda item: built.append(object()),
        )
        monkeypatch.setattr(
            handler._folder_stack,
            "_position_stack_window",
            lambda: built.append(object()) or built[-1],
        )
        handler._folder_stack._folder_stack_window = window
        handler._folder_stack._folder_stack_revealer = revealer
        handler._folder_stack._folder_stack_item = item

        result = handler._folder_stack._refresh_folder_stack()

        assert result is False
        assert window.visible is True

    def test_schedule_folder_stack_prewarm_deduplicates_target(
        self, handler, monkeypatch
    ):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        idle_calls: list[object] = []
        monkeypatch.setattr(
            menu_mod.GLib, "idle_add", lambda callback: idle_calls.append(callback) or 9
        )

        handler._folder_stack.schedule_prewarm(item)
        handler._folder_stack.schedule_prewarm(item)

        assert len(idle_calls) == 1
        assert len(handler._folder_stack._folder_stack_cache.prewarm_queue) == 1

    def test_folder_stack_transition_type_matches_position(self, handler):
        handler._config.pos = "bottom"
        assert (
            handler._folder_stack._stack_transition_type()
            == menu_mod.Gtk.RevealerTransitionType.SLIDE_UP
        )
        handler._config.pos = "top"
        assert (
            handler._folder_stack._stack_transition_type()
            == menu_mod.Gtk.RevealerTransitionType.SLIDE_DOWN
        )
        handler._config.pos = "left"
        assert (
            handler._folder_stack._stack_transition_type()
            == menu_mod.Gtk.RevealerTransitionType.SLIDE_RIGHT
        )
        handler._config.pos = "right"
        assert (
            handler._folder_stack._stack_transition_type()
            == menu_mod.Gtk.RevealerTransitionType.SLIDE_LEFT
        )

    def test_replace_folder_stack_content_replaces_existing_child(self, handler):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        revealer = FakeRevealer()
        stale = FakeBox()
        revealer.add(stale)
        handler._folder_stack._folder_stack_revealer = revealer

        handler._folder_stack._replace_folder_stack_content(item=item)

        assert revealer.get_child() is not stale
        assert revealer.get_child().shown is True

    def test_position_folder_stack_window_supports_all_edges(self, handler):
        child = FakeBox()
        revealer = FakeRevealer()
        revealer.add(child)
        window = FakeWindow()
        handler._folder_stack._folder_stack_window = window
        handler._folder_stack._folder_stack_revealer = revealer
        handler._folder_stack._folder_stack_anchor_x = 120
        handler._folder_stack._folder_stack_anchor_y = 200
        handler._folder_stack._folder_stack_icon_w = 48
        handler._folder_stack._folder_stack_fold_center_x = 40

        handler._folder_stack._folder_stack_position_value = "bottom"
        handler._folder_stack._position_stack_window()
        assert window.moved_to == (104, 158)

        handler._folder_stack._folder_stack_position_value = "top"
        handler._folder_stack._position_stack_window()
        assert window.moved_to == (104, 208)

        handler._folder_stack._folder_stack_position_value = "left"
        handler._folder_stack._position_stack_window()
        assert window.moved_to == (128, 207)

        handler._folder_stack._folder_stack_position_value = "right"
        handler._folder_stack._position_stack_window()
        assert window.moved_to == (0, 207)

    def test_track_folder_stack_handles_invalid_target_and_error(
        self, handler, monkeypatch
    ):
        monkeypatch.setattr(
            menu_mod.launcher_mod, "normalize_file_target", lambda _t: None
        )
        handler._folder_stack._track_folder_stack("invalid")
        assert handler._folder_stack._folder_stack_monitor is None

        warned = MagicMock()
        monkeypatch.setattr(folder_stack_mod.log, "warning", warned)
        monkeypatch.setattr(
            menu_mod.launcher_mod,
            "normalize_file_target",
            lambda _t: "file:///tmp/docs",
        )
        monkeypatch.setattr(menu_mod.GLib, "Error", RuntimeError)

        class _Folder:
            def monitor_directory(self, *_args):
                raise RuntimeError("boom")

        monkeypatch.setattr(menu_mod.Gio.File, "new_for_uri", lambda _uri: _Folder())

        handler._folder_stack._track_folder_stack("file:///tmp/docs")

        warned.assert_called_once()

    def test_track_folder_stack_connects_monitor(self, handler, monkeypatch):
        monitor = FakeMonitor()
        monkeypatch.setattr(
            menu_mod.launcher_mod,
            "normalize_file_target",
            lambda _t: "file:///tmp/docs",
        )

        class _Folder:
            def monitor_directory(self, *_args):
                return monitor

        monkeypatch.setattr(menu_mod.Gio.File, "new_for_uri", lambda _uri: _Folder())

        handler._folder_stack._track_folder_stack("file:///tmp/docs")

        assert handler._folder_stack._folder_stack_monitor is monitor
        assert monitor.changed[0] == "changed"

    def test_track_folder_stack_cancels_previous_monitor(self, handler, monkeypatch):
        previous = FakeMonitor()
        replacement = FakeMonitor()
        handler._folder_stack._folder_stack_monitor = previous
        monkeypatch.setattr(
            menu_mod.launcher_mod,
            "normalize_file_target",
            lambda _t: "file:///tmp/docs",
        )

        class _Folder:
            def monitor_directory(self, *_args):
                return replacement

        monkeypatch.setattr(menu_mod.Gio.File, "new_for_uri", lambda _uri: _Folder())

        handler._folder_stack._track_folder_stack("file:///tmp/docs")

        assert previous.cancelled is True
        assert handler._folder_stack._folder_stack_monitor is replacement

    def test_repeated_hover_show_does_not_retrack_folder(self, handler, monkeypatch):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        window = FakeWindow()
        window.visible = True
        handler._folder_stack._folder_stack_window = window
        handler._folder_stack._stack_owner_id = item.desktop_id
        tracked = MagicMock()
        monkeypatch.setattr(handler._folder_stack, "_track_folder_stack", tracked)

        handler._folder_stack.show(
            item=item,
            anchor_x=100,
            anchor_y=200,
            icon_w=48,
            position="bottom",
            toggle_if_same_item=False,
        )

        tracked.assert_not_called()

    def test_folder_content_cache_is_bounded(self, handler, monkeypatch):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        monkeypatch.setattr(
            handler._folder_stack._browser,
            "target_state",
            lambda _target: "ok",
        )
        stamps = iter(
            range(folder_stack_mod.FOLDER_STACK_CONTENT_CACHE_MAX_ENTRIES + 1)
        )
        monkeypatch.setattr(
            handler._folder_stack._browser,
            "cache_stamp",
            lambda _target: next(stamps),
        )
        monkeypatch.setattr(
            handler._folder_stack,
            "_list_directory_rows",
            lambda **_kwargs: [
                {
                    "target": "file:///tmp/docs/file",
                    "name": "file",
                    "icon": None,
                }
            ],
        )

        for _ in range(folder_stack_mod.FOLDER_STACK_CONTENT_CACHE_MAX_ENTRIES + 1):
            handler._folder_stack._stack_content_for_item(item=item, icon_px=48)

        assert (
            len(handler._folder_stack._folder_content_cache)
            == folder_stack_mod.FOLDER_STACK_CONTENT_CACHE_MAX_ENTRIES
        )

    def test_refresh_folder_stack_returns_false_without_window_or_item(self, handler):
        handler._folder_stack._folder_stack_window = None
        handler._folder_stack._folder_stack_item = None

        assert handler._folder_stack._refresh_folder_stack() is False

    def test_folder_stack_cards_for_missing_and_empty_folder(
        self, handler, monkeypatch
    ):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        monkeypatch.setattr(
            handler._folder_stack._browser, "target_state", lambda _target: "missing"
        )

        cards, popup_w, popup_h = handler._folder_stack._folder_stack_cards_for_item(
            item
        )

        assert cards[0].label == "Folder not found"
        assert popup_w > 0
        assert popup_h > 0

        monkeypatch.setattr(
            handler._folder_stack._browser, "target_state", lambda _target: "ok"
        )
        monkeypatch.setattr(
            handler._folder_stack, "_list_directory_rows", lambda **_kwargs: []
        )

        cards, popup_w, popup_h = handler._folder_stack._folder_stack_cards_for_item(
            item
        )

        assert cards[0].label == "Folder is empty"
        assert popup_w > 0
        assert popup_h > 0

    def test_draw_folder_stack_card_returns_when_geometry_missing(
        self, handler, monkeypatch
    ):
        cr = MagicMock()
        monkeypatch.setattr(
            handler._folder_stack,
            "_stack_card_geometry",
            lambda **_kwargs: None,
        )

        handler._folder_stack._draw_stack_card(
            cr=cr,
            card=folder_stack_mod.FolderStackCard(
                label="x",
                target=None,
                icon=None,
                icon_x=0,
                icon_y=0,
                icon_size=0,
                label_x=0,
                label_y=0,
                label_w=10,
                label_h=10,
                centered=False,
            ),
            sequence_index=0,
            now_us=0,
        )

        cr.save.assert_not_called()

    def test_draw_folder_stack_card_scales_icon_and_draws_action_arrow(
        self, handler, monkeypatch
    ):
        geometry = folder_stack_mod.FolderStackCardGeometry(
            reveal=1.0,
            hover_value=0.2,
            rotation_radians=0.1,
            icon_x=10,
            icon_y=20,
            icon_size=24,
            icon_center_x=22,
            icon_center_y=32,
            label_x=30,
            label_y=40,
        )
        scaled = FakePixbuf(24, 24)
        pixbuf = FakePixbuf(48, 48, scaled=scaled)
        cr = MagicMock()
        monkeypatch.setattr(
            handler._folder_stack,
            "_stack_card_geometry",
            lambda **_kwargs: geometry,
        )
        monkeypatch.setattr(stack_mod, "rounded_rect", MagicMock())
        monkeypatch.setattr(
            stack_mod.Gdk,
            "cairo_set_source_pixbuf",
            MagicMock(),
        )
        monkeypatch.setattr(
            stack_mod,
            "GdkPixbuf",
            SimpleNamespace(InterpType=SimpleNamespace(BILINEAR=1)),
        )

        handler._folder_stack._draw_stack_card(
            cr=cr,
            card=folder_stack_mod.FolderStackCard(
                label="Open Folder",
                target="file:///tmp/docs",
                icon=pixbuf,
                icon_x=10,
                icon_y=20,
                icon_size=48,
                label_x=30,
                label_y=40,
                label_w=100,
                label_h=24,
                centered=True,
            ),
            sequence_index=0,
            now_us=0,
        )

        assert pixbuf.scale_calls == [(24, 24, 1)]
        assert cr.paint_with_alpha.call_count == 2
        assert cr.stroke.call_count >= 1

        cr.reset_mock()
        handler._folder_stack._draw_stack_card(
            cr=cr,
            card=folder_stack_mod.FolderStackCard(
                label="Open Folder",
                target="file:///tmp/docs",
                icon=None,
                icon_x=0,
                icon_y=0,
                icon_size=0,
                label_x=30,
                label_y=40,
                label_w=100,
                label_h=24,
                centered=True,
            ),
            sequence_index=0,
            now_us=0,
        )

        assert cr.stroke.call_count >= 2

    def test_folder_stack_card_at_and_button_mismatch_paths(self, handler, monkeypatch):
        top = folder_stack_mod.FolderStackCard(
            label="Top",
            target="file:///tmp/top",
            icon=None,
            icon_x=80,
            icon_y=20,
            icon_size=20,
            label_x=0,
            label_y=0,
            label_w=100,
            label_h=24,
            centered=False,
        )
        bottom = folder_stack_mod.FolderStackCard(
            label="Bottom",
            target="file:///tmp/bottom",
            icon=None,
            icon_x=0,
            icon_y=0,
            icon_size=0,
            label_x=0,
            label_y=0,
            label_w=100,
            label_h=24,
            centered=False,
        )
        handler._folder_stack._folder_stack_cards = [bottom, top]
        monkeypatch.setattr(
            handler._folder_stack,
            "_stack_card_geometry",
            lambda *, card, **_kwargs: folder_stack_mod.FolderStackCardGeometry(
                reveal=1.0,
                hover_value=0.0,
                rotation_radians=0.0,
                icon_x=card.icon_x,
                icon_y=card.icon_y,
                icon_size=card.icon_size,
                icon_center_x=0.0,
                icon_center_y=0.0,
                label_x=card.label_x,
                label_y=card.label_y,
            ),
        )

        assert handler._folder_stack._stack_card_at(10, 10) is top
        assert (
            handler._folder_stack._on_stack_button_press(
                FakeDrawingArea(), SimpleNamespace(x=10.0, y=10.0, button=2)
            )
            is False
        )
        handler._folder_stack._folder_stack_pressed_target = "file:///tmp/top"
        assert (
            handler._folder_stack._on_stack_button_release(
                FakeDrawingArea(), SimpleNamespace(x=200.0, y=200.0, button=1)
            )
            is False
        )

    def test_folder_stack_motion_leave_and_animation_helpers(
        self, handler, monkeypatch
    ):
        card = folder_stack_mod.FolderStackCard(
            label="doc",
            target="file:///tmp/doc",
            icon=None,
            icon_x=0,
            icon_y=0,
            icon_size=0,
            label_x=0,
            label_y=0,
            label_w=50,
            label_h=20,
            centered=False,
        )
        monkeypatch.setattr(
            handler._folder_stack, "_stack_card_at", lambda *_args: card
        )
        handler._folder_stack._folder_stack_area = FakeDrawingArea()
        timeout_calls: list[tuple[int, object]] = []
        monkeypatch.setattr(
            menu_mod.GLib,
            "timeout_add",
            lambda delay, cb: timeout_calls.append((delay, cb)) or 33,
        )

        assert (
            handler._folder_stack._on_stack_motion_notify(
                FakeDrawingArea(), SimpleNamespace(x=1.0, y=2.0)
            )
            is False
        )
        assert handler._folder_stack._folder_stack_hover_target == "file:///tmp/doc"
        assert handler._folder_stack._folder_stack_anim_source == 33
        assert handler._folder_stack._folder_stack_area.draw_queued is True

        assert (
            handler._folder_stack._on_stack_leave_notify(FakeDrawingArea(), MagicMock())
            is False
        )
        assert handler._folder_stack._folder_stack_hover_target is None
        assert handler._folder_stack._folder_stack_pressed_target is None

    def test_folder_stack_animation_frame_paths(self, handler, monkeypatch):
        handler._folder_stack._folder_stack_area = FakeDrawingArea()
        handler._folder_stack._folder_stack_window = FakeWindow()
        handler._folder_stack._folder_stack_window.hide()

        assert handler._folder_stack._on_stack_animation_frame() is False
        assert handler._folder_stack._folder_stack_anim_source == 0

        handler._folder_stack._folder_stack_window.show_all()
        handler._folder_stack._folder_stack_show_started_us = 0
        handler._folder_stack._folder_stack_cards = [
            folder_stack_mod.FolderStackCard(
                label="doc",
                target="file:///tmp/doc",
                icon=None,
                icon_x=0,
                icon_y=0,
                icon_size=0,
                label_x=0,
                label_y=0,
                label_w=50,
                label_h=20,
                centered=False,
            )
        ]
        handler._folder_stack._folder_stack_hover_target = "file:///tmp/doc"
        handler._folder_stack._folder_stack_hover_values = {"file:///tmp/doc": 0.99}
        monkeypatch.setattr(menu_mod.GLib, "get_monotonic_time", lambda: 1_000_000)

        assert handler._folder_stack._on_stack_animation_frame() is False
        assert (
            handler._folder_stack._folder_stack_hover_values["file:///tmp/doc"] == 1.0
        )

        handler._folder_stack._folder_stack_show_started_us = 900_000
        handler._folder_stack._folder_stack_hover_target = "file:///tmp/doc"
        handler._folder_stack._folder_stack_hover_values = {"file:///tmp/doc": 0.0}

        assert handler._folder_stack._on_stack_animation_frame() is True
        assert handler._folder_stack._folder_stack_area.draw_queued is True

    def test_folder_stack_reveal_open_and_target_state_helpers(
        self, handler, monkeypatch
    ):
        handler._folder_stack._folder_stack_show_started_us = 0
        assert (
            handler._folder_stack._stack_reveal_progress(sequence_index=1, now_us=100)
            == 1.0
        )

        handler._folder_stack._folder_stack_show_started_us = 1_000_000
        assert (
            handler._folder_stack._stack_reveal_progress(
                sequence_index=10, now_us=1_000_000
            )
            == 0.0
        )
        assert (
            0.0
            < handler._folder_stack._stack_reveal_progress(
                sequence_index=0, now_us=1_100_000
            )
            <= 1.0
        )

        opened: list[str] = []
        monkeypatch.setattr(menu_mod.launcher_mod, "open_target", opened.append)
        monkeypatch.setattr(
            handler._folder_stack,
            "_close_stack",
            lambda: opened.append("closed"),
        )
        handler._folder_stack._open_folder_stack_target("file:///tmp/docs")
        assert opened == ["file:///tmp/docs", "closed"]

        monkeypatch.setattr(
            menu_mod.launcher_mod, "normalize_file_target", lambda _t: None
        )
        assert handler._folder_stack._browser.target_state("invalid") == "missing"

        monkeypatch.setattr(
            menu_mod.launcher_mod,
            "normalize_file_target",
            lambda _t: "file:///tmp/docs",
        )

        class _BadFolder:
            def query_exists(self, _arg):
                raise RuntimeError("boom")

        monkeypatch.setattr(menu_mod.Gio.File, "new_for_uri", lambda _uri: _BadFolder())
        assert (
            handler._folder_stack._browser.target_state("file:///tmp/docs") == "missing"
        )

    def test_folder_menu_submenu_tracking_cleanup_and_helpers(
        self, handler, monkeypatch
    ):
        folder_item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
            prefs_key="file:///tmp/docs",
        )
        menu = FakeMenu()
        submenu = FakeMenu()
        row = FakeMenuItem("row")
        row.set_submenu(submenu)
        menu.append(row)
        submenu.append(FakeMenuItem("child"))
        monitor = FakeMonitor()
        handler._folder_menu_monitors[id(submenu)] = monitor
        handler._folder_menu_refresh_sources[id(submenu)] = 9
        handler._folder_menu_context[id(submenu)] = (
            submenu,
            folder_item,
            folder_item.target,
            False,
        )
        removed: list[int] = []
        monkeypatch.setattr(
            menu_mod.GLib, "source_remove", lambda source: removed.append(source)
        )

        handler._clear_menu_children(menu)

        assert removed == [9]
        assert monitor.cancelled is True
        assert menu.get_children() == []

        populate = MagicMock()
        handler._track_folder_menu = MagicMock()
        handler._populate_directory_menu = populate
        handler._on_folder_submenu_show(FakeMenu(), folder_item, folder_item.target)
        populate.assert_called_once()

        existing_menu = FakeMenu()
        existing_menu.append(FakeMenuItem("existing"))
        populate.reset_mock()
        handler._on_folder_submenu_show(existing_menu, folder_item, folder_item.target)
        populate.assert_not_called()

        row = {"kind": 1, "name": "B", "size": 2, "created": 3, "modified": 4}
        assert handler._folder_stack._browser.sort_key(row, "kind") == (1, "b")
        assert handler._folder_stack._browser.sort_key(row, "size") == (2, "b")
        assert handler._folder_stack._browser.sort_key(row, "created") == (3, "b")
        assert handler._folder_stack._browser.sort_key(row, "modified") == (4, "b")
        assert handler._folder_stack._browser.sort_key(row, "name") == ("b",)

        assert handler._folder_stack.icon_px(folder_item) == 16

    def test_directory_has_visible_children_and_sort_callback_paths(
        self, handler, monkeypatch
    ):
        class _Info:
            def __init__(self, hidden: bool) -> None:
                self.hidden = hidden

            def get_is_hidden(self) -> bool:
                return self.hidden

        class _Enumerator:
            def __init__(self, infos) -> None:
                self._infos = list(infos)

            def next_file(self, _arg):
                return self._infos.pop(0) if self._infos else None

        class _Folder:
            def __init__(self, infos) -> None:
                self._infos = infos

            def enumerate_children(self, *_args):
                return _Enumerator(self._infos)

        monkeypatch.setattr(
            menu_mod.launcher_mod,
            "normalize_file_target",
            lambda _t: "file:///tmp/docs",
        )
        monkeypatch.setattr(
            menu_mod.Gio.File,
            "new_for_uri",
            lambda _uri: _Folder([_Info(True), _Info(False)]),
        )
        assert (
            handler._folder_stack._browser.directory_has_visible_children(
                "file:///tmp/docs", False
            )
            is True
        )

        monkeypatch.setattr(
            menu_mod.Gio.File, "new_for_uri", lambda _uri: _Folder([_Info(True)])
        )
        assert (
            handler._folder_stack._browser.directory_has_visible_children(
                "file:///tmp/docs", False
            )
            is False
        )
        assert (
            handler._folder_stack._browser.directory_has_visible_children(
                "file:///tmp/docs", True
            )
            is True
        )

        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
            prefs_key="file:///tmp/docs",
        )
        active = FakeCheckMenuItem("Sort")
        active.set_active(True)
        inactive = FakeCheckMenuItem("Sort")
        inactive.set_active(False)
        update_pref = MagicMock()
        monkeypatch.setattr(handler, "_update_folder_pref", update_pref)

        handler._on_folder_sort_changed(inactive, item, "created")
        handler._on_folder_sort_changed(active, item, "modified")

        update_pref.assert_called_once_with(item, "sort", "modified")
