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

import docking.ui.menu as menu_mod
from docking.core.items import FILE_KIND, FOLDER_KIND
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
    def __init__(self) -> None:
        self.pixel_size = None

    @classmethod
    def new_from_pixbuf(cls, _pixbuf):
        return cls()

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
    monkeypatch.setattr(menu_mod, "PangoCairo", FakePangoCairo)
    monkeypatch.setattr(menu_mod, "load_catalog_icon", lambda applet_id, size: None)
    about = MagicMock()
    settings = MagicMock()
    runtime = MagicMock()
    runtime.cursor_position.return_value = (20.0, 8.0)
    frame = _frame()

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
        save=MagicMock(),
    )
    tracker = MagicMock()
    tracker.get_windows_for.return_value = []
    tracker.get_window_title_for_xid.side_effect = lambda xid: f"Window {xid}"
    launcher = MagicMock()
    launcher.default_directory_app_name.return_value = None
    return menu_mod.MenuHandler(
        about=about,
        settings=settings,
        runtime=runtime,
        model=model,
        config=config,
        window_tracker=tracker,
        launcher=launcher,
        geometry_builder=SimpleNamespace(build_frame=lambda **_kwargs: frame),
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
        assert "Large Icons" in labels

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
            handler,
            "_list_directory",
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
        assert "Large Icons" in labels
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
            handler,
            "_list_directory",
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
            handler, "_directory_has_visible_children", lambda **_kwargs: False
        )
        handler._launcher.resolve_file_icon.return_value = "folder-pixbuf"
        item = DockItem(
            desktop_id="file:///tmp/root",
            kind=FOLDER_KIND,
            target="file:///tmp/root",
            prefs_key="file:///tmp/root",
        )

        rows = handler._list_directory(folder_item=item, target="file:///tmp/root")

        assert rows[0]["icon"] == "folder-pixbuf"
        handler._launcher.resolve_file_icon.assert_called_once_with(
            target="file:///tmp/docs",
            gicon=gicon,
            content_type="inode/directory",
            size=16,
            is_dir=True,
        )

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
        window = MagicMock()
        window.get_xid.return_value = 7
        handler._tracker.get_windows_for.return_value = [window]
        handler._tracker.get_window_title_for_xid.return_value = "A" * 80
        monkeypatch.setattr(menu_mod, "capture_window", lambda **_kwargs: "thumb")
        monkeypatch.setattr(menu_mod.launcher_mod, "get_actions", lambda **_kwargs: [])

        handler._build_item_menu(menu=menu, item=item)

        row = menu.children[0]
        assert isinstance(row.get_child(), FakeBox)
        assert isinstance(row.get_child().children[0], FakeImage)
        assert isinstance(row.get_child().children[1], FakeLabel)
        assert (
            row.get_child().children[1].max_width_chars == menu_mod.MENU_LABEL_MAX_CHARS
        )
        close_label = row.get_child().children[2]
        assert isinstance(close_label, FakeLabel)
        assert close_label.label == "×"

        close_event = SimpleNamespace(x=170.0)
        assert row.emit("button-press-event", close_event) is True
        assert row.emit("button-release-event", close_event) is True
        handler._tracker.close_xid.assert_called_once_with(7)
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
        handler._tracker.activate_xid.assert_called_once_with(7)


class TestDockMenu:
    def test_build_dock_menu_wires_separator_quit_and_applets(
        self, handler, monkeypatch
    ):
        # Given
        menu = FakeMenu()
        FakeGtk.main_quit.reset_mock()
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
        assert "Preferences" in labels
        assert "About" in labels
        assert "Quit" in labels
        assert "Auto-hide" not in labels
        assert "Window Previews" not in labels
        assert "Icons" not in labels
        assert "Themes" not in labels
        assert "Position" not in labels
        assert labels.index("Preferences") == labels.index("About") - 1
        assert labels.index("About") == labels.index("Quit") - 1

        next(mi for mi in menu.children if mi.get_label() == "Add Separator").activate()
        handler._model.add_separator.assert_called_once_with(index=3)

        show_about = MagicMock()
        handler._about.show = show_about
        show_settings = MagicMock()
        handler._settings.show = show_settings
        next(mi for mi in menu.children if mi.get_label() == "Preferences").activate()
        show_settings.assert_called_once()

        next(mi for mi in menu.children if mi.get_label() == "About").activate()
        show_about.assert_called_once()

        next(mi for mi in menu.children if mi.get_label() == "Quit").activate()
        FakeGtk.main_quit.assert_called_once()

        applets_item = next(
            mi for mi in menu.children if mi.get_label() == menu_mod._("Add Applet")
        )
        submenu_labels = _labels(applets_item.get_submenu())
        assert "Time & Productivity" in submenu_labels
        item = next(
            mi
            for mi in applets_item.get_submenu().get_children()
            if mi.get_label() == "Calendar"
        )
        item.activate()
        handler._model.add_applet.assert_called_once_with("calendar")

    def test_show_builds_background_menu_and_pops_at_pointer(
        self, handler, monkeypatch
    ):
        # Given
        event = SimpleNamespace(x=10.0, y=5.0)
        frame = _frame(item=None, insert_index=1)
        handler._geometry_builder = SimpleNamespace(build_frame=lambda **_kwargs: frame)
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
        handler.show(event=event, cursor_main=10.0)
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
        handler._geometry_builder = SimpleNamespace(
            build_frame=lambda **_kwargs: _frame(item=item)
        )
        built: list[tuple[str, object]] = []

        def capture_build(*, menu, item):
            built.append(("item", item))

        monkeypatch.setattr(handler, "_build_item_menu", capture_build)

        handler.show(event=event, cursor_main=20.0)

        assert built == [("item", item)]
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
        handler._on_folder_large_icons_toggled(toggle, item)

        assert handler._config.item_prefs["file:///tmp/docs"]["show_hidden"] is True
        assert handler._config.item_prefs["file:///tmp/docs"]["large_icons"] is True
        assert handler._config.save.call_count >= 2

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

    def test_show_folder_stack_builds_popup_window(self, handler, monkeypatch):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        monkeypatch.setattr(menu_mod.GLib, "timeout_add", lambda *_args: 1)
        monkeypatch.setattr(handler, "_folder_target_state", lambda _target: "ok")
        monkeypatch.setattr(
            handler,
            "_list_directory",
            lambda **_kwargs: [
                {
                    "target": "file:///tmp/docs/readme.txt",
                    "name": "readme.txt",
                    "is_dir": False,
                    "icon": None,
                }
            ],
        )
        tracked: list[str] = []
        monkeypatch.setattr(
            handler, "_track_folder_stack", lambda target: tracked.append(target)
        )

        handler.show_folder_stack(
            item=item,
            anchor_x=120,
            anchor_y=800,
            icon_w=48,
            position="bottom",
        )

        window = FakeWindow.last_created
        assert window is not None
        assert window.visible is True
        assert window.moved_to is not None
        assert tracked == ["file:///tmp/docs"]
        handler._runtime.menu_popup_opened.assert_called_once()
        handler._runtime.hide_hover_ui.assert_called_once()

    def test_show_folder_stack_second_click_toggles_closed(self, handler, monkeypatch):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        monkeypatch.setattr(menu_mod.GLib, "timeout_add", lambda *_args: 1)
        monkeypatch.setattr(handler, "_folder_target_state", lambda _target: "ok")
        monkeypatch.setattr(handler, "_list_directory", lambda **_kwargs: [])
        monkeypatch.setattr(handler, "_track_folder_stack", lambda target: None)

        handler.show_folder_stack(
            item=item,
            anchor_x=120,
            anchor_y=800,
            icon_w=48,
            position="bottom",
        )
        window = FakeWindow.last_created

        handler.show_folder_stack(
            item=item,
            anchor_x=120,
            anchor_y=800,
            icon_w=48,
            position="bottom",
        )

        assert window is not None
        assert window.visible is False
        assert handler._runtime.menu_popup_opened.call_count == 1
        handler._runtime.menu_popup_closed.assert_called_once()

    def test_folder_stack_requests_dock_sized_icons(self, handler, monkeypatch):
        handler._config.icon_size = 52
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        requested_icon_sizes: list[int] = []

        monkeypatch.setattr(handler, "_folder_target_state", lambda _target: "ok")
        monkeypatch.setattr(
            handler,
            "_list_directory",
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

        cards, _popup_w, _popup_h = handler._folder_stack_cards_for_item(item)

        assert requested_icon_sizes == [52]
        assert cards[1].icon_size == 52
        assert cards[1].label_w <= menu_mod.FOLDER_STACK_LABEL_MAX_WIDTH_PX

    def test_folder_stack_action_chip_allows_wider_more_label(
        self, handler, monkeypatch
    ):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        monkeypatch.setattr(handler, "_folder_target_state", lambda _target: "ok")
        handler._launcher.default_directory_app_name.return_value = "Caja"
        monkeypatch.setattr(
            handler,
            "_list_directory",
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

        cards, _popup_w, _popup_h = handler._folder_stack_cards_for_item(item)

        assert cards[0].label == "5 More in Caja"
        expected_width = (
            menu_mod._measure_stack_text_px("5 More in Caja")
            + 2 * menu_mod.FOLDER_STACK_LABEL_TEXT_MARGIN_PX
            + menu_mod.FOLDER_STACK_ACTION_ARROW_GAP_PX
            + menu_mod.FOLDER_STACK_ACTION_ARROW_SIZE_PX
            + 10
        )
        assert cards[0].label_w == handler._folder_stack_action_width(
            label="5 More in Caja"
        )
        assert cards[0].label_w == expected_width
        assert cards[0].label_w <= menu_mod.FOLDER_STACK_ACTION_MAX_WIDTH_PX

    def test_folder_stack_action_chip_falls_back_without_directory_app(
        self, handler, monkeypatch
    ):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        monkeypatch.setattr(handler, "_folder_target_state", lambda _target: "ok")
        handler._launcher.default_directory_app_name.return_value = None
        monkeypatch.setattr(
            handler,
            "_list_directory",
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

        cards, _popup_w, _popup_h = handler._folder_stack_cards_for_item(item)

        assert cards[0].label == "5 More in Folder"

    def test_folder_stack_short_labels_fit_chip_width(self, handler, monkeypatch):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        monkeypatch.setattr(handler, "_folder_target_state", lambda _target: "ok")
        monkeypatch.setattr(
            handler,
            "_list_directory",
            lambda **_kwargs: [
                {
                    "target": "file:///tmp/docs/doc",
                    "name": "doc",
                    "is_dir": True,
                    "icon": object(),
                }
            ],
        )

        cards, _popup_w, _popup_h = handler._folder_stack_cards_for_item(item)

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
        monkeypatch.setattr(handler, "_folder_target_state", lambda _target: "ok")
        monkeypatch.setattr(
            handler,
            "_list_directory",
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

        cards, popup_w, _popup_h = handler._folder_stack_cards_for_item(item)

        icon_cards = [card for card in cards if card.icon_size > 0]
        assert len(icon_cards) == 4
        fold_center_x = handler._folder_stack_fold_center_x
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
        monkeypatch.setattr(handler, "_folder_target_state", lambda _target: "ok")
        monkeypatch.setattr(
            handler,
            "_list_directory",
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

        cards, _popup_w, _popup_h = handler._folder_stack_cards_for_item(item)

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
        handler._folder_stack_refresh_source = 12

        handler._on_folder_stack_changed(MagicMock(), MagicMock(), None, MagicMock())

        assert removed == [12]
        assert timeout_calls[0][0] == 120
        assert handler._folder_stack_refresh_source == 77

    def test_folder_stack_click_opens_target(self, handler):
        target = "file:///tmp/docs/readme.txt"
        handler._folder_stack_cards = [
            menu_mod.FolderStackCard(
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
        with patch.object(handler, "_open_folder_stack_target", opened.append):
            press = SimpleNamespace(x=32.0, y=60.0, button=1)
            release = SimpleNamespace(x=32.0, y=60.0, button=1)

            assert (
                handler._on_folder_stack_button_press(FakeDrawingArea(), press) is True
            )
            assert (
                handler._on_folder_stack_button_release(FakeDrawingArea(), release)
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
            handler,
            "_replace_folder_stack_content",
            lambda item: built.append(object()),
        )
        monkeypatch.setattr(
            handler,
            "_position_folder_stack_window",
            lambda: built.append(object()) or built[-1],
        )
        handler._folder_stack_window = window
        handler._folder_stack_revealer = revealer
        handler._folder_stack_item = item

        result = handler._refresh_folder_stack()

        assert result is False
        assert window.visible is True
