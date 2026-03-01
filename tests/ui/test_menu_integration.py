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

import docking.applets  # noqa: E402
import docking.ui.menu as menu_mod  # noqa: E402
from docking.platform.model import DockItem  # noqa: E402


class FakeMenu:
    def __init__(self) -> None:
        self.children: list[FakeMenuItem] = []
        self.shown = False
        self.popup_event = None

    def append(self, item) -> None:
        self.children.append(item)

    def get_children(self):
        return list(self.children)

    def show_all(self) -> None:
        self.shown = True

    def popup_at_pointer(self, event) -> None:
        self.popup_event = event


class FakeMenuItem:
    def __init__(self, label: str = "") -> None:
        self._label = label
        self._submenu = None
        self._signals: dict[str, list[tuple[object, tuple[object, ...]]]] = {}

    def connect(self, signal: str, callback, *args) -> None:
        self._signals.setdefault(signal, []).append((callback, args))

    def activate(self) -> None:
        for callback, args in self._signals.get("activate", []):
            callback(self, *args)
        for callback, args in self._signals.get("toggled", []):
            callback(self, *args)

    def set_submenu(self, submenu) -> None:
        self._submenu = submenu

    def get_submenu(self):
        return self._submenu

    def get_label(self) -> str:
        return self._label

    def get_active(self) -> bool:
        return True


class FakeCheckMenuItem(FakeMenuItem):
    def __init__(self, label: str = "") -> None:
        super().__init__(label=label)
        self._active = False

    def set_active(self, active: bool) -> None:
        self._active = active

    def get_active(self) -> bool:
        return self._active


class FakeRadioMenuItem(FakeCheckMenuItem):
    def join_group(self, _other) -> None:
        return


class FakeSeparatorMenuItem(FakeMenuItem):
    def __init__(self) -> None:
        super().__init__(label="---")


class FakeGtk:
    Menu = FakeMenu
    MenuItem = FakeMenuItem
    CheckMenuItem = FakeCheckMenuItem
    RadioMenuItem = FakeRadioMenuItem
    SeparatorMenuItem = FakeSeparatorMenuItem
    main_quit = MagicMock()


def _labels(menu: FakeMenu) -> list[str]:
    return [child.get_label() for child in menu.get_children()]


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setattr(menu_mod, "Gtk", FakeGtk)
    window = MagicMock()
    window.theme = MagicMock(item_padding=8, h_padding=12)
    window.local_cursor_main.return_value = 20.0
    window.zoomed_main_offset.return_value = 0.0
    window.autohide = MagicMock()
    window._dnd = MagicMock()

    model = MagicMock()
    model.pinned_items = []
    config = SimpleNamespace(
        lock_icons=False,
        autohide=True,
        previews_enabled=True,
        theme="default",
        icon_size=48,
        position="bottom",
        save=MagicMock(),
    )
    tracker = MagicMock()
    return menu_mod.MenuHandler(
        window=window,
        model=model,
        config=config,
        tracker=tracker,
        launcher=MagicMock(),
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


class TestDockMenu:
    def test_build_dock_menu_wires_separator_quit_and_applets(
        self, handler, monkeypatch
    ):
        # Given
        menu = FakeMenu()
        FakeGtk.main_quit.reset_mock()
        handler._model.pinned_items = [DockItem(desktop_id="applet://clock")]
        monkeypatch.setattr(
            docking.applets,
            "get_registry",
            lambda: {
                "clock": SimpleNamespace(name="Clock"),
                "separator": SimpleNamespace(name="Separator"),
            },
        )

        # When
        handler._build_dock_menu(menu=menu, insert_index=3)
        labels = _labels(menu)
        # Then
        assert "Auto-hide" in labels
        assert "Window Previews" in labels
        assert "Lock Icons" in labels
        assert "Add Separator" in labels
        assert "Quit" in labels
        assert "Applets" in labels

        next(mi for mi in menu.children if mi.get_label() == "Add Separator").activate()
        handler._model.add_separator.assert_called_once_with(index=3)

        next(mi for mi in menu.children if mi.get_label() == "Quit").activate()
        FakeGtk.main_quit.assert_called_once()

        applets_item = next(mi for mi in menu.children if mi.get_label() == "Applets")
        check = applets_item.get_submenu().get_children()[0]
        check.set_active(False)
        check.activate()
        handler._model.remove_applet.assert_called_once_with("applet://clock")

    def test_show_builds_background_menu_and_pops_at_pointer(
        self, handler, monkeypatch
    ):
        # Given
        event = object()
        handler._model.visible_items.return_value = [DockItem(desktop_id="x.desktop")]
        monkeypatch.setattr(
            menu_mod,
            "compute_layout",
            lambda *args, **kwargs: [SimpleNamespace(x=0, width=48, scale=1.0)],
        )
        monkeypatch.setattr(handler, "_hit_test", lambda *args, **kwargs: None)
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
        handler._window.autohide.reset.assert_called_once()
        handler._window.update_struts.assert_called_once()

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
        assert handler._window.theme is new_theme
        handler._window.update_struts.assert_called()
        handler._window.drawing_area.queue_draw.assert_called()

        pos_widget = FakeCheckMenuItem("Position")
        pos_widget.set_active(True)
        handler._on_position_changed(pos_widget, "left")
        assert handler._config.position == "left"
        handler._window.reposition.assert_called_once()

        size_widget = FakeCheckMenuItem("Icon Size")
        size_widget.set_active(True)
        handler._on_icon_size_changed(size_widget, 64)
        assert handler._config.icon_size == 64

    def test_hit_test_and_insert_index(self, handler):
        # Given
        handler._window.zoomed_main_offset.return_value = 0.0
        items = [DockItem(desktop_id="a.desktop"), DockItem(desktop_id="b.desktop")]
        layout = [
            SimpleNamespace(x=0, width=48, scale=1.0),
            SimpleNamespace(x=70, width=48, scale=1.0),
        ]

        found = handler._hit_test(main_coord=20, items=items, layout=layout)
        # Then
        assert found is items[0]

        # When
        idx = handler._insert_index(cursor_main=40, items=items, layout=layout)
        assert idx == 1
