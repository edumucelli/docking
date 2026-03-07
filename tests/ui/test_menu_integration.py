"""Integration-style tests for MenuHandler behavior."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

try:
    import gi  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover - fallback for non-GI environments
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

import docking.ui.menu as menu_mod  # noqa: E402
from docking.core.items import FILE_KIND, FOLDER_KIND  # noqa: E402
from docking.platform.model import DockItem  # noqa: E402


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


class FakePopover:
    last_created = None

    def __init__(self, relative_to=None) -> None:
        self.relative_to = relative_to
        self.child = None
        self.modal = False
        self.position = None
        self.pointing_to = None
        self.popped = False
        self.parent = None
        self._signals: dict[str, list[tuple[object, tuple[object, ...]]]] = {}
        FakePopover.last_created = self

    @classmethod
    def new(cls, relative_to):
        return cls(relative_to=relative_to)

    def set_modal(self, value: bool) -> None:
        self.modal = value

    def set_position(self, value) -> None:
        self.position = value

    def connect(self, signal: str, callback, *args) -> None:
        self._signals.setdefault(signal, []).append((callback, args))

    def set_pointing_to(self, rect) -> None:
        self.pointing_to = rect

    def get_child(self):
        return self.child

    def remove(self, _child) -> None:
        self.child = None

    def add(self, child) -> None:
        self.child = child
        if hasattr(child, "parent"):
            child.parent = self

    def show_all(self) -> None:
        return

    def popup(self) -> None:
        self.popped = True

    def popdown(self) -> None:
        self.popped = False
        for callback, args in self._signals.get("closed", []):
            callback(self, *args)


class FakeAlign:
    FILL = 0


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
    Button = FakeButton
    CheckMenuItem = FakeCheckMenuItem
    CheckButton = FakeCheckButton
    RadioMenuItem = FakeRadioMenuItem
    SeparatorMenuItem = FakeSeparatorMenuItem
    Separator = FakeSeparator
    ScrolledWindow = FakeScrolledWindow
    ComboBoxText = FakeComboBoxText
    Popover = FakePopover
    Image = FakeImage
    Box = FakeBox
    Label = FakeLabel
    Orientation = FakeOrientation
    Align = FakeAlign
    PolicyType = FakePolicyType
    ReliefStyle = FakeReliefStyle
    PositionType = FakePositionType
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
    about = MagicMock()
    runtime = MagicMock()
    runtime.get_monitor_menu_choices.return_value = []
    runtime.current_monitor_choice.return_value = -1
    runtime.primary_monitor_index.return_value = 0
    runtime.cursor_position.return_value = (20.0, 8.0)
    frame = _frame()

    model = MagicMock()
    model.pinned_items = []
    config = SimpleNamespace(
        lock_icons=False,
        autohide=True,
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
    return menu_mod.MenuHandler(
        about=about,
        runtime=runtime,
        model=model,
        config=config,
        window_tracker=tracker,
        launcher=MagicMock(),
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
        handler._launcher.load_gicon.return_value = "folder-pixbuf"
        handler._launcher.load_icon.return_value = "fallback-pixbuf"
        item = DockItem(
            desktop_id="file:///tmp/root",
            kind=FOLDER_KIND,
            target="file:///tmp/root",
            prefs_key="file:///tmp/root",
        )

        rows = handler._list_directory(folder_item=item, target="file:///tmp/root")

        assert rows[0]["icon"] == "folder-pixbuf"
        handler._launcher.load_gicon.assert_called_once()

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
            "get_registry",
            lambda: {
                "clock": SimpleNamespace(name="Clock", icon_name="clock"),
                "separator": SimpleNamespace(name="Separator", icon_name="list-remove"),
            },
        )

        # When
        handler._build_dock_menu(menu=menu, insert_index=3)
        labels = _labels(menu)
        # Then
        assert "Auto-hide" in labels
        assert "Window Previews" in labels
        assert "Display" not in labels
        assert "Icons" in labels
        assert "Add Separator" in labels
        assert "About" in labels
        assert "Quit" in labels
        assert "Applets" in labels
        assert labels.index("About") == labels.index("Quit") - 1

        icons_item = next(mi for mi in menu.children if mi.get_label() == "Icons")
        icons_labels = _labels(icons_item.get_submenu())
        assert "Lock Positions" in icons_labels
        assert "Current Workspace Only" in icons_labels
        assert "Change Size" in icons_labels
        assert "Show Tooltips" in icons_labels

        next(mi for mi in menu.children if mi.get_label() == "Add Separator").activate()
        handler._model.add_separator.assert_called_once_with(index=3)

        show_about = MagicMock()
        handler._about.show = show_about
        next(mi for mi in menu.children if mi.get_label() == "About").activate()
        show_about.assert_called_once()

        next(mi for mi in menu.children if mi.get_label() == "Quit").activate()
        FakeGtk.main_quit.assert_called_once()

        applets_item = next(mi for mi in menu.children if mi.get_label() == "Applets")
        submenu_labels = _labels(applets_item.get_submenu())
        assert "Time & Productivity" in submenu_labels
        check = next(
            mi
            for mi in applets_item.get_submenu().get_children()
            if mi.get_label() == "Clock"
        )
        check.set_active(False)
        check.activate()
        handler._model.remove_applet.assert_called_once_with("applet://clock")

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

    def test_build_dock_menu_shows_display_submenu_for_multiple_monitors(self, handler):
        # Given
        menu = FakeMenu()
        handler._runtime.get_monitor_menu_choices.return_value = [
            ("Display 1: 1920x1080 (Primary)", 0),
            ("Display 2: 2560x1440", 1),
        ]
        handler._runtime.current_monitor_choice.return_value = 0

        # When
        handler._build_dock_menu(menu=menu, insert_index=0)
        labels = _labels(menu)

        # Then
        assert "Display" in labels
        display_item = next(mi for mi in menu.children if mi.get_label() == "Display")
        submenu_children = display_item.get_submenu().get_children()
        assert submenu_children[0].get_label() == "Follow Cursor"
        assert submenu_children[0].get_active() is False
        assert submenu_children[1].get_label() == "---"
        assert submenu_children[2].get_label() == "Display 1: 1920x1080 (Primary)"
        assert submenu_children[3].get_label() == "Display 2: 2560x1440"

    def test_display_submenu_disables_radio_when_follow_cursor_enabled(self, handler):
        # Given
        menu = FakeMenu()
        handler._config.active_display = True
        handler._runtime.get_monitor_menu_choices.return_value = [
            ("Display 1: 1920x1080 (Primary)", 0),
            ("Display 2: 2560x1440", 1),
        ]
        handler._runtime.current_monitor_choice.return_value = 0

        # When
        handler._build_dock_menu(menu=menu, insert_index=0)

        # Then
        display_item = next(mi for mi in menu.children if mi.get_label() == "Display")
        submenu_children = display_item.get_submenu().get_children()
        assert submenu_children[0].get_label() == "Follow Cursor"
        assert submenu_children[0].get_active() is True
        assert submenu_children[2]._sensitive is False
        assert submenu_children[3]._sensitive is False


class TestMenuCallbacks:
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

    def test_autohide_toggle_resets_and_updates_struts(self, handler):
        # Given
        widget = FakeCheckMenuItem("Auto-hide")
        widget.set_active(False)
        # When
        handler._on_autohide_toggled(widget)
        # Then
        assert handler._config.autohide is False
        handler._config.save.assert_called_once()
        handler._runtime.reset_autohide.assert_called_once()
        handler._runtime.update_struts.assert_called_once()

    def test_theme_position_and_size_callbacks(self, handler, monkeypatch):
        # Given
        widget = FakeCheckMenuItem("Theme")
        widget.set_active(True)
        new_theme = object()
        monkeypatch.setattr(menu_mod.Theme, "load", lambda name, _size: new_theme)
        # When
        handler._on_theme_changed(widget, "solar")
        # Then
        assert handler._config.theme == "solar"
        handler._runtime.set_theme.assert_called_once_with(new_theme)
        handler._runtime.reposition.assert_called_once()
        handler._runtime.queue_draw.assert_called()

        pos_widget = FakeCheckMenuItem("Position")
        pos_widget.set_active(True)
        handler._on_position_changed(pos_widget, "left")
        assert handler._config.position == "left"
        assert handler._runtime.reposition.call_count == 2

        size_widget = FakeCheckMenuItem("Icon Size")
        size_widget.set_active(True)
        handler._on_icon_size_changed(size_widget, 64)
        assert handler._config.icon_size == 64

    def test_monitor_changed_repositions_and_saves(self, handler):
        # Given
        widget = FakeCheckMenuItem("Display")
        widget.set_active(True)
        handler._config.monitor_index = -1

        # When
        handler._on_monitor_changed(widget, 1)

        # Then
        assert handler._config.monitor_index == 1
        handler._config.save.assert_called_once()
        handler._runtime.reposition.assert_called_once()

    def test_monitor_changed_primary_persists_as_follow_primary(self, handler):
        # Given
        widget = FakeCheckMenuItem("Display")
        widget.set_active(True)
        handler._config.monitor_index = 1
        handler._runtime.primary_monitor_index.return_value = 0

        # When
        handler._on_monitor_changed(widget, 0)

        # Then
        assert handler._config.monitor_index == -1
        handler._config.save.assert_called_once()
        handler._runtime.reposition.assert_called_once()

    def test_hit_test_and_insert_index(self, handler):
        # Given
        items = [DockItem(desktop_id="a.desktop"), DockItem(desktop_id="b.desktop")]
        handler._runtime.cursor_position.return_value = (20.0, 8.0)
        frame = _frame(item=items[0], item_index=0, insert_index=1)

        found = handler._hit_test(main_coord=20, items=items, frame=frame)
        # Then
        assert found is items[0]

        # When
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
