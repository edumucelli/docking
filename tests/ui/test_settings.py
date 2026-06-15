"""Tests for the settings window controller."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

try:
    import gi  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover - fallback for non-GI environments
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    gi_mock.repository.GLib.markup_escape_text.side_effect = lambda text: text
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

import docking.ui.settings as settings_mod


def _catalog_entry(*, applet_id, name: str, category=None):
    return settings_mod.AppletMeta(
        id=str(applet_id),
        name=name,
        category=category or settings_mod.AppletCategory.OTHER,
    )


class FakeStyleContext:
    def add_class(self, _name: str) -> None:
        pass


class FakeLabel:
    def __init__(self, label: str = "") -> None:
        self._label = label
        self.markup = None
        self.xalign = 0.0
        self.hexpand = False
        self.margin_top = 0
        self.margin_bottom = 0
        self.line_wrap = False
        self.max_width_chars = -1
        self.tooltip_text = None

    def get_label(self) -> str:
        return self._label

    def set_label(self, value: str) -> None:
        self._label = value

    def set_markup(self, value: str) -> None:
        self.markup = value

    def set_xalign(self, value: float) -> None:
        self.xalign = value

    def set_hexpand(self, value: bool) -> None:
        self.hexpand = value

    def set_line_wrap(self, value: bool) -> None:
        self.line_wrap = value

    def set_margin_top(self, value: int) -> None:
        self.margin_top = value

    def set_margin_bottom(self, value: int) -> None:
        self.margin_bottom = value

    def set_max_width_chars(self, value: int) -> None:
        self.max_width_chars = value

    def set_line_wrap_mode(self, mode: int) -> None:
        pass

    def set_tooltip_text(self, value: str) -> None:
        self.tooltip_text = value

    def get_style_context(self) -> FakeStyleContext:
        return FakeStyleContext()

    def set_size_request(self, width: int, height: int) -> None:
        pass


class FakeBox:
    def __init__(self, **_kwargs) -> None:
        self.children: list[object] = []
        self.border_width = 0
        self.hexpand = False
        self.size_request = None
        self.margin_start = 0
        self.margin_end = 0
        self.tooltip_text = None

    def set_border_width(self, value: int) -> None:
        self.border_width = value

    def set_margin_start(self, value: int) -> None:
        self.margin_start = value

    def set_margin_end(self, value: int) -> None:
        self.margin_end = value

    def set_hexpand(self, value: bool) -> None:
        self.hexpand = value

    def set_size_request(self, width: int, height: int) -> None:
        self.size_request = (width, height)

    def set_tooltip_text(self, value: str) -> None:
        self.tooltip_text = value

    def pack_start(self, child, *_args) -> None:
        self.children.append(child)

    def pack_end(self, child, *_args) -> None:
        self.children.append(child)

    def get_children(self) -> list[object]:
        return list(self.children)

    def remove(self, child) -> None:
        if child in self.children:
            self.children.remove(child)


class FakeGrid:
    def __init__(self) -> None:
        self.children: list[object] = []
        self.attachments: list[tuple[object, int, int, int, int]] = []
        self.column_spacing = 0
        self.row_spacing = 0
        self.column_homogeneous = False

    def set_column_spacing(self, value: int) -> None:
        self.column_spacing = value

    def set_row_spacing(self, value: int) -> None:
        self.row_spacing = value

    def set_column_homogeneous(self, value: bool) -> None:
        self.column_homogeneous = value

    def attach(self, child, left: int, top: int, width: int, height: int) -> None:
        self.children.append(child)
        self.attachments.append((child, left, top, width, height))


class FakeWindow:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.child = None
        self.callbacks: dict[str, object] = {}
        self.show_count = 0
        self.present_count = 0
        self.position = None

    def set_default_size(self, *_args) -> None:
        return

    def set_modal(self, *_args) -> None:
        return

    def set_resizable(self, *_args) -> None:
        return

    def set_position(self, value) -> None:
        self.position = value

    def connect(self, signal: str, callback) -> None:
        self.callbacks[signal] = callback

    def add(self, child) -> None:
        self.child = child

    def set_hexpand(self, value: bool) -> None:
        self.hexpand = value

    def set_size_request(self, width: int, height: int) -> None:
        self.size_request = (width, height)

    def show_all(self) -> None:
        self.show_count += 1

    def present(self) -> None:
        self.present_count += 1


class FakeNotebook:
    def __init__(self) -> None:
        self.pages: list[tuple[object, object]] = []
        self.scrollable = False

    def set_scrollable(self, value: bool) -> None:
        self.scrollable = value

    def append_page(self, child, tab_label) -> None:
        self.pages.append((child, tab_label))


class FakeStack:
    def __init__(self) -> None:
        self.pages: list[tuple[object, str, str]] = []

    def add_titled(self, child, name: str, title: str) -> None:
        self.pages.append((child, name, title))


class FakeStackSwitcher:
    def __init__(self) -> None:
        self.stack = None
        self.halign = None

    def set_stack(self, stack) -> None:
        self.stack = stack

    def set_halign(self, value) -> None:
        self.halign = value


class FakeComboBoxText:
    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []
        self._active_id: str | None = None
        self.callbacks: dict[str, object] = {}
        self.sensitive = True
        self.tooltip_text = None

    def append(self, item_id: str, text: str) -> None:
        self.items.append((item_id, text))

    def remove_all(self) -> None:
        self.items.clear()
        self._active_id = None

    def connect(self, signal: str, callback) -> None:
        self.callbacks[signal] = callback

    def set_active_id(self, value: str) -> None:
        self._active_id = value

    def set_size_request(self, width: int, height: int) -> None:
        pass

    def get_active_id(self) -> str | None:
        return self._active_id

    def emit_changed(self) -> None:
        callback = self.callbacks.get("changed")
        if callback is not None:
            callback(self)

    def set_sensitive(self, value: bool) -> None:
        self.sensitive = value

    def set_tooltip_text(self, value: str) -> None:
        self.tooltip_text = value


class FakeSpinButton:
    def __init__(self) -> None:
        self._value = 0.0
        self.callbacks: dict[str, object] = {}
        self.properties: dict[str, object] = {}
        self.sensitive = True
        self.tooltip_text = None

    @classmethod
    def new_with_range(cls, *_args):
        return cls()

    def connect(self, signal: str, callback) -> None:
        self.callbacks[signal] = callback

    def set_property(self, name: str, value: object) -> None:
        self.properties[name] = value

    def set_value(self, value: float) -> None:
        self._value = value

    def get_value(self) -> float:
        return self._value

    def emit_value_changed(self) -> None:
        callback = self.callbacks.get("value-changed")
        if callback is not None:
            callback(self)

    def set_sensitive(self, value: bool) -> None:
        self.sensitive = value

    def set_size_request(self, width: int, height: int) -> None:
        self.size_request = (width, height)

    def set_tooltip_text(self, value: str) -> None:
        self.tooltip_text = value


class FakeScale(FakeSpinButton):
    @classmethod
    def new_with_range(cls, *_args):
        return cls()

    def set_digits(self, value: int) -> None:
        self.digits = value

    def set_draw_value(self, value: bool) -> None:
        self.draw_value = value

    def set_hexpand(self, value: bool) -> None:
        self.hexpand = value


class FakeSwitch:
    def __init__(self) -> None:
        self._active = False
        self.callbacks: dict[str, object] = {}
        self.sensitive = True
        self.tooltip_text = None

    def connect(self, signal: str, callback, *args) -> None:
        self.callbacks[signal] = (callback, args)

    def set_active(self, active: bool) -> None:
        self._active = active

    def get_active(self) -> bool:
        return self._active

    def emit_notify_active(self) -> None:
        callback, args = self.callbacks["notify::active"]
        callback(self, *args)

    def set_sensitive(self, value: bool) -> None:
        self.sensitive = value

    def set_tooltip_text(self, value: str) -> None:
        self.tooltip_text = value


class FakeCheckButton(FakeSwitch):
    def __init__(self, label: str = "") -> None:
        super().__init__()
        self._label = label
        self.tooltip_text = None
        self.child = None
        self.hexpand = False
        self.size_request = None

    def get_label(self) -> str:
        return self._label

    def set_tooltip_text(self, value: str) -> None:
        self.tooltip_text = value

    def add(self, child) -> None:
        self.child = child

    def set_hexpand(self, value: bool) -> None:
        self.hexpand = value

    def set_size_request(self, width: int, height: int) -> None:
        self.size_request = (width, height)


class FakeButton:
    def __init__(self, label: str = "") -> None:
        self.label = label
        self.callbacks: dict[str, object] = {}

    def connect(self, signal: str, callback) -> None:
        self.callbacks[signal] = callback

    def click(self) -> None:
        callback = self.callbacks.get("clicked")
        if callback is not None:
            callback(self)


class FakeImage:
    def __init__(self, source: object) -> None:
        self.source = source
        self.pixel_size = None
        self.size_request = None
        self.tooltip_text = None

    @classmethod
    def new_from_icon_name(cls, icon_name: str, icon_size):
        return cls(("icon", icon_name, icon_size))

    @classmethod
    def new_from_pixbuf(cls, pixbuf):
        return cls(("pixbuf", pixbuf))

    def set_pixel_size(self, value: int) -> None:
        self.pixel_size = value

    def set_size_request(self, width: int, height: int) -> None:
        self.size_request = (width, height)

    def set_tooltip_text(self, value: str) -> None:
        self.tooltip_text = value


class FakePixbuf:
    def __init__(self, name: str, width: int = 16, height: int = 16) -> None:
        self.name = name
        self.width = width
        self.height = height

    def get_width(self) -> int:
        return self.width

    def get_height(self) -> int:
        return self.height

    def scale_simple(self, width: int, height: int, _interp) -> FakePixbuf:
        return FakePixbuf(self.name, width=width, height=height)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FakePixbuf):
            return False
        return (
            self.name == other.name
            and self.width == other.width
            and self.height == other.height
        )


class FakeEventBox:
    def __init__(self) -> None:
        self.child = None
        self.visible_window = True
        self.size_request = None
        self.tooltip_text = None
        self.events = 0
        self.callbacks: dict[str, object] = {}

    def set_visible_window(self, value: bool) -> None:
        self.visible_window = value

    def set_size_request(self, width: int, height: int) -> None:
        self.size_request = (width, height)

    def add_events(self, events: int) -> None:
        self.events |= events

    def connect(self, signal: str, callback) -> None:
        self.callbacks[signal] = callback

    def set_tooltip_text(self, value: str) -> None:
        self.tooltip_text = value

    def add(self, child) -> None:
        self.child = child

    def emit_enter(self) -> None:
        callback = self.callbacks.get("enter-notify-event")
        if callback is not None:
            callback(self, object())

    def emit_leave(self) -> None:
        callback = self.callbacks.get("leave-notify-event")
        if callback is not None:
            callback(self, object())


class FakePopover:
    def __init__(self, relative_to) -> None:
        self.relative_to = relative_to
        self.child = None
        self.modal = True
        self.position = None
        self.popup_count = 0
        self.popdown_count = 0
        self.show_all_count = 0

    @classmethod
    def new(cls, relative_to):
        return cls(relative_to)

    def set_modal(self, value: bool) -> None:
        self.modal = value

    def set_position(self, value) -> None:
        self.position = value

    def add(self, child) -> None:
        self.child = child

    def show_all(self) -> None:
        self.show_all_count += 1

    def popup(self) -> None:
        self.popup_count += 1

    def popdown(self) -> None:
        self.popdown_count += 1


class FakeScrolledWindow:
    def __init__(self) -> None:
        self.child = None

    def set_policy(self, *_args) -> None:
        return

    def add(self, child) -> None:
        self.child = child

    def set_hexpand(self, value: bool) -> None:
        self.hexpand = value

    def set_size_request(self, width: int, height: int) -> None:
        self.size_request = (width, height)


class FakeOrientation:
    HORIZONTAL = 0
    VERTICAL = 1


class FakePolicyType:
    NEVER = 0
    AUTOMATIC = 1


class FakeAlign:
    CENTER = 0


class FakeIconSize:
    MENU = 0


class FakePositionType:
    TOP = 0


class FakeEventMask:
    ENTER_NOTIFY_MASK = 1
    LEAVE_NOTIFY_MASK = 2


class FakeWindowPosition:
    CENTER = 0


class FakeGtkSettings:
    current = None

    def __init__(self) -> None:
        self.properties = {"gtk-im-module": None}

    @classmethod
    def get_default(cls):
        if cls.current is None:
            cls.current = cls()
        return cls.current

    def get_property(self, name: str):
        return self.properties.get(name)

    def set_property(self, name: str, value: object) -> None:
        self.properties[name] = value


class FakeGtk:
    Window = FakeWindow
    Notebook = FakeNotebook
    Stack = FakeStack
    StackSwitcher = FakeStackSwitcher
    Label = FakeLabel
    Box = FakeBox
    Grid = FakeGrid
    ComboBoxText = FakeComboBoxText
    SpinButton = FakeSpinButton
    Scale = FakeScale
    Switch = FakeSwitch
    CheckButton = FakeCheckButton
    Button = FakeButton
    Image = FakeImage
    EventBox = FakeEventBox
    Popover = FakePopover
    ScrolledWindow = FakeScrolledWindow
    Orientation = FakeOrientation
    PolicyType = FakePolicyType
    Align = FakeAlign
    IconSize = FakeIconSize
    PositionType = FakePositionType
    WindowPosition = FakeWindowPosition
    Settings = FakeGtkSettings


class FakeGdk:
    EventMask = FakeEventMask


def _config():
    return SimpleNamespace(
        hide_mode="autohide",
        previews_enabled=True,
        tooltips_enabled=True,
        left_click_action="toggle",
        middle_click_action="new-window",
        folder_stack_unfold="click",
        window_list_sort="default",
        show_window_count_numbers=False,
        lock_icons=False,
        current_workspace_only=False,
        active_display=False,
        monitor_index=-1,
        monitor_connector=None,
        anchor_applets=False,
        anchor_files=False,
        zoom_enabled=True,
        theme="default",
        transparency=1.0,
        position="bottom",
        icon_size=48,
        zoom_percent=1.5,
        hide_delay_ms=0,
        unhide_delay_ms=0,
        update_check_enabled=True,
        update_check_interval_hours=24,
        additional_distance_from_edge=0,
        pressure_reveal_enabled=False,
        pressure_threshold=50,
        save=MagicMock(),
    )


class TestSettingsWindowController:
    def test_show_reuses_single_window_and_builds_four_tabs(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(settings_mod, "Gdk", FakeGdk)
        monkeypatch.setattr(
            settings_mod, "load_catalog_icon", lambda applet_id, size: None
        )
        monkeypatch.setattr(settings_mod, "get_applet_catalog", dict)
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=MagicMock(),
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=_config(),
        )

        controller.show()
        window = controller._window
        assert window is not None
        controller.show()

        assert controller._window is window
        assert window.show_count == 2
        assert window.present_count == 2
        assert window.position == FakeWindowPosition.CENTER
        switcher, stack = window.child.children
        assert isinstance(switcher, FakeStackSwitcher)
        assert switcher.halign == FakeAlign.CENTER
        assert switcher.stack is stack
        assert [title for _, _, title in stack.pages] == [
            "Appearance",
            "Behavior",
            "Applets",
            "Updates",
        ]
        appearance_box = stack.pages[0][0]
        section_labels = [
            child.get_children()[0].markup
            for child in appearance_box.get_children()
            if isinstance(child, FakeBox) and child.get_children()
        ]
        assert section_labels == [
            "<b>Look</b>",
            "<b>Placement</b>",
            "<b>Monitor</b>",
            "<b>Layout</b>",
        ]
        behavior_box = stack.pages[1][0]
        behavior_labels = [
            child.get_children()[0].markup
            for child in behavior_box.get_children()
            if isinstance(child, FakeBox) and child.get_children()
        ]
        assert behavior_labels == [
            "<b>Mouse</b>",
            "<b>Behavior</b>",
            "<b>Folder Stacks</b>",
        ]
        updates_box = stack.pages[3][0]
        updates_labels = [
            child.get_children()[0].markup
            for child in updates_box.get_children()
            if isinstance(child, FakeBox) and child.get_children()
        ]
        assert updates_labels == ["<b>Update Checks</b>"]

    def test_numeric_spin_buttons_use_simple_im_context(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(settings_mod, "Gdk", FakeGdk)
        monkeypatch.setattr(
            settings_mod, "load_catalog_icon", lambda applet_id, size: None
        )
        monkeypatch.setattr(settings_mod, "get_applet_catalog", dict)
        FakeGtkSettings.current = None
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=MagicMock(),
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=_config(),
        )

        controller.show()

        assert controller._icon_size_spin.properties["im-module"] == (
            "gtk-im-context-simple"
        )
        assert controller._zoom_percent_spin.properties["im-module"] == (
            "gtk-im-context-simple"
        )
        assert controller._hide_delay_spin.properties["im-module"] == (
            "gtk-im-context-simple"
        )
        assert controller._unhide_delay_spin.properties["im-module"] == (
            "gtk-im-context-simple"
        )
        assert FakeGtkSettings.get_default().properties["gtk-im-module"] is None

    def test_hide_controls_exist_only_in_behavior_tab(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(settings_mod, "Gdk", FakeGdk)
        monkeypatch.setattr(
            settings_mod, "load_catalog_icon", lambda applet_id, size: None
        )
        monkeypatch.setattr(settings_mod, "get_applet_catalog", dict)
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=MagicMock(),
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=_config(),
        )

        controller.show()
        stack = controller._window.child.children[1]
        appearance_box = stack.pages[0][0]
        behavior_box = stack.pages[1][0]

        def row_labels(tab_box):
            labels = []
            for section in tab_box.get_children():
                if not isinstance(section, FakeBox):
                    continue
                children = section.get_children()
                if len(children) < 2 or not isinstance(children[1], FakeBox):
                    continue
                content = children[1]
                for row in content.get_children():
                    if isinstance(row, FakeBox) and row.get_children():
                        title = row.get_children()[0]
                        if isinstance(title, FakeLabel):
                            labels.append(title.get_label())
            return labels

        appearance_rows = row_labels(appearance_box)
        behavior_rows = row_labels(behavior_box)

        assert "Hide Mode" not in appearance_rows
        assert "Hide Delay" not in appearance_rows
        assert "Unhide Delay" not in appearance_rows
        assert "Open On" not in appearance_rows
        assert "Follow Cursor" in appearance_rows
        assert "Monitor" in appearance_rows
        assert "Hide Mode" in behavior_rows
        assert "Hide Delay" in behavior_rows
        assert "Unhide Delay" in behavior_rows
        assert "Open On" in behavior_rows

    def test_appearance_and_behavior_rows_have_visible_info_icons(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(settings_mod, "Gdk", FakeGdk)
        monkeypatch.setattr(
            settings_mod, "load_catalog_icon", lambda applet_id, size: None
        )
        monkeypatch.setattr(settings_mod, "get_applet_catalog", dict)
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=MagicMock(),
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=_config(),
        )

        controller.show()
        stack = controller._window.child.children[1]
        appearance_box = stack.pages[0][0]
        behavior_box = stack.pages[1][0]

        def rows_by_label(tab_box):
            rows = {}
            for section in tab_box.get_children():
                if not isinstance(section, FakeBox):
                    continue
                children = section.get_children()
                if len(children) < 2 or not isinstance(children[1], FakeBox):
                    continue
                for row in children[1].get_children():
                    if not isinstance(row, FakeBox) or len(row.get_children()) < 2:
                        continue
                    title = row.get_children()[0]
                    if isinstance(title, FakeLabel):
                        rows[title.get_label()] = row
            return rows

        appearance_rows = rows_by_label(appearance_box)
        behavior_rows = rows_by_label(behavior_box)
        rows = {**appearance_rows, **behavior_rows}
        existing_info_rows = {
            "Extra Distance from Edge",
            "Hide Mode",
            "Monitor",
            "Pressure Threshold",
        }
        expected_tooltip_rows = {
            "Theme",
            "Icon Size",
            "Transparency",
            "Zoom",
            "Zoom Percent",
            "Show Tooltips",
            "Window Previews",
            "Show Window Counts",
            "Position",
            "Follow Cursor",
            "Current Workspace Only",
            "Lock Positions",
            "Anchor Applets to End",
            "Anchor Files to End",
            "Left Click",
            "Middle Click",
            "Window List Sort",
            "Hide Delay",
            "Unhide Delay",
            "Pressure Reveal",
            "Open On",
        }

        for label in expected_tooltip_rows:
            row = rows[label]
            title, control = row.get_children()[:2]
            assert title.tooltip_text is None
            assert isinstance(control, FakeBox)
            control_children = control.get_children()
            assert len(control_children) == 2
            assert isinstance(control_children[1], FakeEventBox)
            assert control_children[1]._docking_info_label.get_label()

        for label in existing_info_rows:
            row = rows[label]
            title, control = row.get_children()[:2]
            assert row.tooltip_text is None
            assert title.tooltip_text is None
            assert getattr(control, "tooltip_text", None) is None

    def test_monitor_selector_saves_connector_and_repositions(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(settings_mod, "Gdk", FakeGdk)
        monkeypatch.setattr(
            settings_mod, "load_catalog_icon", lambda applet_id, size: None
        )
        monkeypatch.setattr(settings_mod, "get_applet_catalog", dict)
        runtime = MagicMock()
        runtime.get_monitor_choices.return_value = [
            SimpleNamespace(label="Display 1 - eDP-1", index=0, connector="eDP-1"),
            SimpleNamespace(label="Display 2 - DP-1", index=1, connector="DP-1"),
        ]
        runtime.current_monitor_choice.return_value = 0
        config = _config()
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=runtime,
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=config,
        )

        controller.show()
        controller._monitor_combo.set_active_id("1")
        controller._monitor_combo.emit_changed()

        assert config.monitor_index == 1
        assert config.monitor_connector == "DP-1"
        config.save.assert_called_once()
        runtime.reposition.assert_called_once()

    def test_follow_cursor_disables_monitor_selector_without_clearing_target(
        self, monkeypatch
    ):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(settings_mod, "Gdk", FakeGdk)
        monkeypatch.setattr(
            settings_mod, "load_catalog_icon", lambda applet_id, size: None
        )
        monkeypatch.setattr(settings_mod, "get_applet_catalog", dict)
        runtime = MagicMock()
        runtime.get_monitor_choices.return_value = [
            SimpleNamespace(label="Display 1 - eDP-1", index=0, connector="eDP-1"),
            SimpleNamespace(label="Display 2 - DP-1", index=1, connector="DP-1"),
        ]
        runtime.current_monitor_choice.return_value = 1
        config = _config()
        config.monitor_index = 1
        config.monitor_connector = "DP-1"
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=runtime,
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=config,
        )

        controller.show()
        controller._active_display_switch.set_active(True)
        controller._active_display_switch.emit_notify_active()

        assert config.active_display is True
        assert config.monitor_index == 1
        assert config.monitor_connector == "DP-1"
        assert controller._monitor_combo.sensitive is False
        runtime.set_active_display.assert_called_once_with(True)
        runtime.reposition.assert_called_once()

    def test_pressure_threshold_uses_info_icon_tooltip(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(settings_mod, "Gdk", FakeGdk)
        monkeypatch.setattr(
            settings_mod, "load_catalog_icon", lambda applet_id, size: None
        )
        monkeypatch.setattr(settings_mod, "get_applet_catalog", dict)
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=MagicMock(),
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=_config(),
        )

        controller.show()
        stack = controller._window.child.children[1]
        behavior_box = stack.pages[1][0]

        pressure_row = None
        for section in behavior_box.get_children():
            if not isinstance(section, FakeBox):
                continue
            children = section.get_children()
            if len(children) < 2 or not isinstance(children[1], FakeBox):
                continue
            for row in children[1].get_children():
                if not isinstance(row, FakeBox) or not row.get_children():
                    continue
                title = row.get_children()[0]
                if isinstance(title, FakeLabel) and title.get_label() == (
                    "Pressure Threshold"
                ):
                    pressure_row = row
                    break

        assert pressure_row is not None
        pressure_box = pressure_row.get_children()[1]
        assert isinstance(pressure_box, FakeBox)
        assert pressure_box.get_children() == [
            controller._pressure_threshold_scale,
            controller._pressure_threshold_info,
        ]
        assert isinstance(controller._pressure_threshold_info, FakeEventBox)
        assert controller._pressure_threshold_info.tooltip_text is None
        assert controller._pressure_threshold_info.events == (
            FakeEventMask.ENTER_NOTIFY_MASK | FakeEventMask.LEAVE_NOTIFY_MASK
        )
        assert "cursor pressure" in (
            controller._pressure_threshold_info._docking_info_label.get_label()
        )

        controller._pressure_threshold_info.emit_enter()
        popover = controller._pressure_threshold_info._docking_info_popover
        assert isinstance(popover.child, FakeBox)
        assert popover.child.border_width == settings_mod.INFO_POPOVER_PADDING_PX
        assert popover.child.get_children() == [
            controller._pressure_threshold_info._docking_info_label
        ]
        assert popover.show_all_count == 1
        assert popover.popup_count == 1
        controller._pressure_threshold_info.emit_leave()
        assert popover.popdown_count == 1

    def test_theme_change_updates_config_and_runtime(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(
            settings_mod, "load_catalog_icon", lambda applet_id, size: None
        )
        monkeypatch.setattr(settings_mod, "get_applet_catalog", dict)
        base_theme = MagicMock()
        applied_theme = object()
        base_theme.with_opacity.return_value = applied_theme
        monkeypatch.setattr(settings_mod.Theme, "load", lambda name, size: base_theme)
        runtime = MagicMock()
        config = _config()
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=runtime,
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=config,
        )

        controller.show()
        widget = controller._theme_combo
        widget.set_active_id("slate")
        widget.emit_changed()

        assert config.theme == "slate"
        config.save.assert_called_once()
        base_theme.with_opacity.assert_called_once_with(config.transparency)
        runtime.set_theme.assert_called_once_with(applied_theme)
        runtime.reposition.assert_called_once()
        runtime.queue_draw.assert_called_once()

    def test_transparency_change_updates_config_and_runtime(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(
            settings_mod, "load_catalog_icon", lambda applet_id, size: None
        )
        monkeypatch.setattr(settings_mod, "get_applet_catalog", dict)
        base_theme = MagicMock()
        applied_theme = object()
        base_theme.with_opacity.return_value = applied_theme
        monkeypatch.setattr(settings_mod.Theme, "load", lambda name, size: base_theme)
        runtime = MagicMock()
        config = _config()
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=runtime,
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=config,
        )

        controller.show()
        controller._transparency_scale.set_value(65)
        controller._transparency_scale.emit_value_changed()

        assert config.transparency == 0.65
        config.save.assert_called_once()
        base_theme.with_opacity.assert_called_once_with(0.65)
        runtime.set_theme.assert_called_once_with(applied_theme)
        runtime.queue_draw.assert_called_once()
        runtime.reposition.assert_not_called()

    def test_mouse_click_action_bindings_update_config(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(
            settings_mod, "load_catalog_icon", lambda applet_id, size: None
        )
        monkeypatch.setattr(settings_mod, "get_applet_catalog", dict)
        runtime = MagicMock()
        config = _config()
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=runtime,
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=config,
        )

        controller.show()
        controller._left_click_combo.set_active_id("most-recent")
        controller._left_click_combo.emit_changed()
        controller._middle_click_combo.set_active_id("close-focused")
        controller._middle_click_combo.emit_changed()

        assert config.left_click_action == "most-recent"
        assert config.middle_click_action == "close-focused"
        assert config.save.call_count == 2
        runtime.assert_not_called()

    def test_folder_stack_unfold_binding_updates_config(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(
            settings_mod, "load_catalog_icon", lambda applet_id, size: None
        )
        monkeypatch.setattr(settings_mod, "get_applet_catalog", dict)
        runtime = MagicMock()
        config = _config()
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=runtime,
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=config,
        )

        controller.show()
        controller._folder_stack_unfold_combo.set_active_id("hover")
        controller._folder_stack_unfold_combo.emit_changed()

        assert config.folder_stack_unfold == "hover"
        config.save.assert_called_once()
        runtime.assert_not_called()

    def test_window_list_sort_binding_updates_config(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(
            settings_mod, "load_catalog_icon", lambda applet_id, size: None
        )
        monkeypatch.setattr(settings_mod, "get_applet_catalog", dict)
        runtime = MagicMock()
        config = _config()
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=runtime,
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=config,
        )

        controller.show()
        controller._window_list_sort_combo.set_active_id("alphabetical")
        controller._window_list_sort_combo.emit_changed()

        assert config.window_list_sort == "alphabetical"
        config.save.assert_called_once()

    def test_show_window_count_numbers_binding_updates_config_and_redraws(
        self, monkeypatch
    ):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(
            settings_mod,
            "load_catalog_icon",
            lambda applet_id, size: None,
        )
        monkeypatch.setattr(settings_mod, "get_applet_catalog", dict)
        runtime = MagicMock()
        config = _config()
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=runtime,
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=config,
        )

        controller.show()
        controller._window_count_numbers_switch.set_active(True)
        controller._window_count_numbers_switch.emit_notify_active()

        assert config.show_window_count_numbers is True
        config.save.assert_called_once()
        runtime.queue_draw.assert_called_once()

    def test_current_workspace_only_updates_surface_scope(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(
            settings_mod,
            "load_catalog_icon",
            lambda applet_id, size: None,
        )
        monkeypatch.setattr(settings_mod, "get_applet_catalog", dict)
        runtime = MagicMock()
        config = _config()
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=runtime,
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=config,
        )

        controller.show()
        controller._workspace_only_switch.set_active(True)
        controller._workspace_only_switch.emit_notify_active()

        assert config.current_workspace_only is True
        config.save.assert_called_once()
        runtime.set_current_workspace_only.assert_called_once_with(True)
        runtime.queue_draw.assert_not_called()

    def test_hide_mode_change_updates_runtime(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(
            settings_mod, "load_catalog_icon", lambda applet_id, size: None
        )
        monkeypatch.setattr(settings_mod, "get_applet_catalog", dict)
        runtime = MagicMock()
        config = _config()
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=runtime,
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=config,
        )

        controller.show()
        widget = controller._hide_mode_combo
        widget.set_active_id("none")
        widget.emit_changed()

        assert config.hide_mode == "none"
        runtime.on_hide_mode_changed.assert_called_once()

    def test_binding_sync_updates_dependent_sensitivity(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(
            settings_mod, "load_catalog_icon", lambda applet_id, size: None
        )
        monkeypatch.setattr(settings_mod, "get_applet_catalog", dict)
        config = _config()
        config.hide_mode = "none"
        config.zoom_enabled = False
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=MagicMock(),
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=config,
        )

        controller.show()

        assert controller._hide_delay_spin.sensitive is False
        assert controller._unhide_delay_spin.sensitive is False
        assert controller._zoom_percent_spin.sensitive is False

    def test_binding_change_updates_config_once_and_runtime(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(
            settings_mod, "load_catalog_icon", lambda applet_id, size: None
        )
        monkeypatch.setattr(settings_mod, "get_applet_catalog", dict)
        runtime = MagicMock()
        config = _config()
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=runtime,
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=config,
        )

        controller.show()
        save_before = config.save.call_count
        controller._zoom_enabled_switch.set_active(False)
        controller._zoom_enabled_switch.emit_notify_active()

        assert config.zoom_enabled is False
        assert config.save.call_count == save_before + 1
        runtime.queue_draw.assert_called_once()
        assert controller._zoom_percent_spin.sensitive is False

    def test_updates_tab_controls_preferences_and_runtime_actions(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(
            settings_mod, "load_catalog_icon", lambda applet_id, size: None
        )
        monkeypatch.setattr(settings_mod, "get_applet_catalog", dict)
        monkeypatch.setattr(
            settings_mod,
            "load_state",
            lambda: SimpleNamespace(last_seen_version="", last_checked_at=""),
        )
        runtime = MagicMock()
        config = _config()
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=runtime,
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=config,
        )

        controller.show()
        controller._update_check_switch.set_active(False)
        controller._update_check_switch.emit_notify_active()
        controller._update_interval_combo.set_active_id("168")
        controller._update_interval_combo.emit_changed()

        assert config.update_check_enabled is False
        assert config.update_check_interval_hours == 168
        assert config.save.call_count == 2
        assert controller._update_status_label.get_label() == "Not checked yet"

        updates_box = controller._window.child.children[1].pages[3][0]
        actions_row = updates_box.children[0].children[1].children[3]
        actions_box = actions_row.children[1]
        check_now, view_releases = actions_box.children
        check_now.click()
        view_releases.click()

        runtime.check_for_updates_now.assert_called_once()
        runtime.open_releases_page.assert_called_once()

    def test_applet_toggle_adds_and_removes_items(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(
            settings_mod, "load_catalog_icon", lambda applet_id, size: None
        )
        monkeypatch.setattr(
            settings_mod,
            "get_applet_catalog",
            lambda: {
                "clock": _catalog_entry(
                    applet_id="clock",
                    name="Clock",
                ),
            },
        )
        model = MagicMock()
        model.pinned_items = []
        model.get_applet.return_value = None
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=MagicMock(),
            model=model,
            config=_config(),
        )

        on_widget = FakeCheckButton(label="Clock")
        on_widget.set_active(True)
        controller._on_applet_toggled(on_widget, "clock")
        model.add_applet.assert_called_once_with("clock")

        off_widget = FakeCheckButton(label="Clock")
        off_widget.set_active(False)
        controller._on_applet_toggled(off_widget, "clock")
        model.remove_applet.assert_called_once_with("applet://clock")

    def test_applet_tab_uses_checkbox_grid_per_category(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(
            settings_mod,
            "load_catalog_icon",
            lambda applet_id, size: FakePixbuf(f"{applet_id}:{size}"),
        )
        monkeypatch.setattr(
            settings_mod,
            "get_applet_catalog",
            lambda: {
                "clock": _catalog_entry(
                    applet_id="clock",
                    name="Clock",
                    category=settings_mod.AppletCategory.PRODUCTIVITY,
                ),
                "weather": _catalog_entry(
                    applet_id="weather",
                    name="Weather",
                    category=settings_mod.AppletCategory.INFORMATION,
                ),
            },
        )
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=MagicMock(),
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=_config(),
        )

        controller.show()

        applets_scroller = controller._window.child.children[1].pages[2][0]
        applets_box = applets_scroller.child
        section_headers = [
            child for child in applets_box.children if isinstance(child, FakeLabel)
        ]
        applet_grids = [
            child for child in applets_box.children if isinstance(child, FakeGrid)
        ]
        assert len(section_headers) == 2
        assert all(header.markup is not None for header in section_headers)
        assert len(applet_grids) == 2
        assert all(grid.column_spacing == 16 for grid in applet_grids)
        assert all(grid.row_spacing == 8 for grid in applet_grids)
        assert all(grid.column_homogeneous is True for grid in applet_grids)
        first_grid = applet_grids[0]
        assert [attachment[1:3] for attachment in first_grid.attachments] == [
            (0, 0),
            (1, 0),
            (2, 0),
        ]
        assert isinstance(first_grid.attachments[1][0], FakeBox)
        assert isinstance(first_grid.attachments[2][0], FakeBox)
        second_grid = applet_grids[1]
        assert [attachment[1:3] for attachment in second_grid.attachments] == [
            (0, 0),
            (1, 0),
            (2, 0),
        ]
        assert isinstance(second_grid.attachments[1][0], FakeBox)
        assert isinstance(second_grid.attachments[2][0], FakeBox)
        first_check = first_grid.children[0]
        assert isinstance(first_check, FakeCheckButton)
        assert isinstance(first_check.child, FakeBox)
        assert isinstance(first_check.child.children[0], FakeImage)
        image = first_check.child.children[0]
        assert image.source[0] == "pixbuf"
        assert image.source[1].name == f"clock:{settings_mod.APPLET_LIST_ICON_PX}"
        assert image.pixel_size == settings_mod.APPLET_LIST_ICON_PX
        assert isinstance(first_check.child.children[1], FakeLabel)
        assert first_check.child.children[1].get_label() == "Clock"

    def test_applet_tab_uses_catalog_icon_assets(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        catalog_loader = MagicMock(return_value=FakePixbuf("clock"))
        monkeypatch.setattr(settings_mod, "load_catalog_icon", catalog_loader)
        monkeypatch.setattr(
            settings_mod,
            "get_applet_catalog",
            lambda: {
                "clock": _catalog_entry(applet_id="clock", name="Clock"),
            },
        )
        model = SimpleNamespace(
            pinned_items=[],
            get_applet=lambda _desktop_id: SimpleNamespace(
                item=SimpleNamespace(icon=FakePixbuf("live-clock", width=32, height=32))
            ),
        )
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=MagicMock(),
            model=model,
            config=_config(),
        )

        controller.show()

        applets_scroller = controller._window.child.children[1].pages[2][0]
        first_check = applets_scroller.child.children[1].children[0]
        assert first_check.child.children[0].source == (
            "pixbuf",
            FakePixbuf("clock"),
        )
        assert catalog_loader.call_count >= 1
        assert catalog_loader.call_args_list[0].kwargs == {
            "applet_id": "clock",
            "size": settings_mod.APPLET_LIST_ICON_PX,
        }

    def test_applet_grid_places_three_items_per_row(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(
            settings_mod,
            "load_catalog_icon",
            lambda applet_id, size: FakePixbuf(f"{applet_id}:{size}"),
        )
        monkeypatch.setattr(
            settings_mod,
            "get_applet_catalog",
            lambda: {
                "clock": _catalog_entry(applet_id="clock", name="Clock"),
                "calendar": _catalog_entry(applet_id="calendar", name="Calendar"),
                "pomodoro": _catalog_entry(applet_id="pomodoro", name="Pomodoro"),
            },
        )
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=MagicMock(),
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=_config(),
        )

        controller.show()

        applets_scroller = controller._window.child.children[1].pages[2][0]
        first_grid = next(
            child
            for child in applets_scroller.child.children
            if isinstance(child, FakeGrid)
        )
        assert [attachment[1:3] for attachment in first_grid.attachments] == [
            (0, 0),
            (1, 0),
            (2, 0),
        ]

    def test_applet_tab_uses_catalog_without_importing_applet_modules(
        self, monkeypatch
    ):
        import docking.applets as applets_mod

        applets_mod.get_applet_catalog.cache_clear()
        applets_mod.get_applet_catalog()
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
        monkeypatch.setattr(
            settings_mod, "load_catalog_icon", lambda applet_id, size: None
        )
        controller = settings_mod.SettingsWindowController(
            parent=object(),
            runtime=MagicMock(),
            model=SimpleNamespace(pinned_items=[], get_applet=lambda _desktop_id: None),
            config=_config(),
        )

        with patch.object(
            applets_mod,
            "import_module",
            side_effect=AssertionError("unexpected import"),
        ):
            controller.show()

        assert controller._applets_box is not None
        assert any(
            isinstance(child, FakeGrid) for child in controller._applets_box.children
        )
        applets_mod.get_applet_catalog.cache_clear()
