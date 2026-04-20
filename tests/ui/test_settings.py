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

    def append(self, item_id: str, text: str) -> None:
        self.items.append((item_id, text))

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


class FakeSpinButton:
    def __init__(self) -> None:
        self._value = 0.0
        self.callbacks: dict[str, object] = {}
        self.properties: dict[str, object] = {}
        self.sensitive = True

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


class FakeImage:
    def __init__(self, source: object) -> None:
        self.source = source
        self.pixel_size = None

    @classmethod
    def new_from_pixbuf(cls, pixbuf):
        return cls(("pixbuf", pixbuf))

    def set_pixel_size(self, value: int) -> None:
        self.pixel_size = value


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
    Image = FakeImage
    ScrolledWindow = FakeScrolledWindow
    Orientation = FakeOrientation
    PolicyType = FakePolicyType
    Align = FakeAlign
    WindowPosition = FakeWindowPosition
    Settings = FakeGtkSettings


def _config():
    return SimpleNamespace(
        hide_mode="autohide",
        previews_enabled=True,
        tooltips_enabled=True,
        left_click_action="toggle",
        middle_click_action="new-window",
        lock_icons=False,
        current_workspace_only=False,
        active_display=False,
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
        save=MagicMock(),
    )


class TestSettingsWindowController:
    def test_show_reuses_single_window_and_builds_three_tabs(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
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
        ]

    def test_numeric_spin_buttons_use_simple_im_context(self, monkeypatch):
        monkeypatch.setattr(settings_mod, "Gtk", FakeGtk)
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
        assert "Hide Mode" in behavior_rows
        assert "Hide Delay" in behavior_rows
        assert "Unhide Delay" in behavior_rows

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
        controller._left_click_combo.set_active_id("cycle")
        controller._left_click_combo.emit_changed()
        controller._middle_click_combo.set_active_id("close-focused")
        controller._middle_click_combo.emit_changed()

        assert config.left_click_action == "cycle"
        assert config.middle_click_action == "close-focused"
        assert config.save.call_count == 2
        runtime.assert_not_called()

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
