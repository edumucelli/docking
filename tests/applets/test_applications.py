"""Tests for the Applications applet."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import docking.applets.applications.applet as applications_applet_mod
import docking.applets.applications.render as applications_render_mod
import docking.applets.apps as apps_shared
from docking.applets.applications.applet import ApplicationsApplet
from docking.applets.applications.state import _build_app_categories
from docking.applets.apps import ApplicationEntry, all_desktop_app_infos
from docking.core.config import Config


class TestBuildAppCategories:
    def test_returns_dict(self):
        categories = _build_app_categories()
        assert isinstance(categories, dict)

    def test_excludes_hidden_apps(self):
        mock_app = MagicMock()
        mock_app.get_is_hidden.return_value = True
        mock_app.get_nodisplay.return_value = False

        with (
            patch(
                "docking.platform.desktop_entries.Gio.AppInfo.get_all",
                return_value=[mock_app],
            ),
            patch(
                "docking.platform.desktop_entries.desktop_dirs",
                return_value=[],
            ),
        ):
            cats = _build_app_categories()
        # Hidden app should not appear in any category
        total = sum(len(apps) for apps in cats.values())
        assert total == 0

    def test_includes_host_desktop_files_not_returned_by_gio(
        self, tmp_path, monkeypatch
    ):
        host_apps = tmp_path / "run" / "host" / "usr" / "share" / "applications"
        host_apps.mkdir(parents=True)
        desktop_file = host_apps / "org.example.Tool.desktop"
        desktop_file.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Example Tool\n"
            "Exec=example-tool\n"
            "Categories=Development;IDE;\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "docking.platform.desktop_entries.Gio.AppInfo.get_all",
            list,
        )
        monkeypatch.setattr(
            "docking.platform.desktop_entries.desktop_dirs",
            lambda: [host_apps],
        )

        categories = _build_app_categories()

        assert list(categories) == ["Development"]
        assert categories["Development"][0].get_display_name() == "Example Tool"

    def test_all_desktop_app_infos_deduplicates_desktop_ids(
        self, tmp_path, monkeypatch
    ):
        first_apps = tmp_path / "first" / "applications"
        second_apps = tmp_path / "second" / "applications"
        first_apps.mkdir(parents=True)
        second_apps.mkdir(parents=True)
        for apps_dir in (first_apps, second_apps):
            (apps_dir / "org.example.Tool.desktop").write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=Example Tool\n"
                "Exec=example-tool\n",
                encoding="utf-8",
            )
        monkeypatch.setattr(
            "docking.platform.desktop_entries.Gio.AppInfo.get_all",
            list,
        )
        monkeypatch.setattr(
            "docking.platform.desktop_entries.desktop_dirs",
            lambda: [first_apps, second_apps],
        )

        apps = all_desktop_app_infos()

        assert [app.get_id() for app in apps] == ["org.example.Tool.desktop"]

    def test_application_entry_launch_uses_launcher_bridge(self, monkeypatch):
        launched: list[str] = []
        monkeypatch.setattr(
            apps_shared,
            "launch_desktop_id",
            lambda *, desktop_id: launched.append(desktop_id),
        )
        entry = ApplicationEntry(
            desktop_id="org.example.Tool.desktop",
            name="Example Tool",
            categories="Utility;",
            icon_name="",
        )

        entry.launch([], None)

        assert launched == ["org.example.Tool.desktop"]

    def test_application_entry_desktop_file_uri_uses_app_info_filename(self, tmp_path):
        desktop_file = tmp_path / "org.example.Tool.desktop"
        desktop_file.write_text("[Desktop Entry]\nType=Application\n", encoding="utf-8")
        app_info = MagicMock()
        app_info.get_filename.return_value = str(desktop_file)
        entry = ApplicationEntry(
            desktop_id="org.example.Tool.desktop",
            name="Example Tool",
            categories="Utility;",
            icon_name="",
            app_info=app_info,
        )

        assert entry.desktop_file_uri() == desktop_file.as_uri()

    def test_application_entry_desktop_file_uri_falls_back_to_lookup(
        self, tmp_path, monkeypatch
    ):
        desktop_file = tmp_path / "org.example.Tool.desktop"
        desktop_file.write_text("[Desktop Entry]\nType=Application\n", encoding="utf-8")
        monkeypatch.setattr(
            apps_shared.desktop_entries,
            "find_desktop_file",
            lambda _desktop_id: desktop_file,
        )
        entry = ApplicationEntry(
            desktop_id="org.example.Tool.desktop",
            name="Example Tool",
            categories="Utility;",
            icon_name="",
        )

        assert entry.desktop_file_uri() == desktop_file.as_uri()


class TestApplicationsApplet:
    def _fake_gtk(self, monkeypatch):
        class _FakeMenu:
            def __init__(self) -> None:
                self.children: list[object] = []
                self.shown = False
                self.popup_event = None
                self._signals: dict[str, list[object]] = {}

            def append(self, item) -> None:
                self.children.append(item)
                item.parent = self

            def remove(self, item) -> None:
                self.children.remove(item)

            def get_children(self) -> list[object]:
                return list(self.children)

            def show_all(self) -> None:
                self.shown = True

            def connect(self, signal: str, callback) -> None:
                self._signals.setdefault(signal, []).append(callback)

            def popup_at_pointer(self, event) -> None:
                self.popup_event = event

        class _FakeMenuItem:
            def __init__(self, label: str = "") -> None:
                self._label = label
                self._submenu = None
                self._signals: dict[str, list[object]] = {}
                self._child = None
                self.visible = True
                self.parent = None
                self.drag_source_args = None
                self.reserve_indicator = False

            def get_label(self) -> str:
                return self._label

            def connect(self, signal: str, callback, *args) -> None:
                self._signals.setdefault(signal, []).append((callback, args))

            def drag_source_set(self, *args) -> None:
                self.drag_source_args = args

            def set_submenu(self, submenu) -> None:
                self._submenu = submenu

            def get_submenu(self):
                return self._submenu

            def add(self, child) -> None:
                self._child = child

            def set_reserve_indicator(self, value: bool) -> None:
                self.reserve_indicator = value

            def show(self) -> None:
                self.visible = True

            def show_all(self) -> None:
                self.visible = True

            def hide(self) -> None:
                self.visible = False

        class _FakeSeparatorMenuItem(_FakeMenuItem):
            pass

        class _FakeBox:
            def __init__(self, **_kwargs) -> None:
                self.children: list[object] = []

            def pack_start(self, child, *_args) -> None:
                self.children.append(child)

        class _FakeEntry:
            def __init__(self) -> None:
                self.placeholder = ""
                self.width_chars = 0
                self.text = ""
                self.focused = False
                self._signals: dict[str, list[object]] = {}

            def set_placeholder_text(self, text: str) -> None:
                self.placeholder = text

            def set_width_chars(self, value: int) -> None:
                self.width_chars = value

            def connect(self, signal: str, callback) -> None:
                self._signals.setdefault(signal, []).append(callback)

            def get_text(self) -> str:
                return self.text

            def set_text(self, text: str) -> None:
                self.text = text

            def emit(self, signal: str) -> None:
                for callback in self._signals.get(signal, []):
                    callback(self)

            def grab_focus(self) -> None:
                self.focused = True

        monkeypatch.setattr(
            applications_applet_mod,
            "Gtk",
            SimpleNamespace(
                Menu=_FakeMenu,
                MenuItem=_FakeMenuItem,
                SeparatorMenuItem=_FakeSeparatorMenuItem,
                Box=_FakeBox,
                Entry=_FakeEntry,
                Orientation=SimpleNamespace(HORIZONTAL=0),
            ),
        )
        monkeypatch.setattr(
            applications_applet_mod,
            "make_menu_item_with_icon",
            lambda label, **_kwargs: _FakeMenuItem(label),
        )

    def test_creates_with_icon(self):
        d = ApplicationsApplet(48, config=Config())
        assert d.item.icon is not None

    def test_left_click_opens_launcher_menu(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        d = ApplicationsApplet(48, config=Config())
        d.on_clicked()

        assert d._popup_menu is not None
        assert d._popup_menu.shown is True
        assert d._popup_menu.popup_event is None

    def test_launcher_menu_contains_search_entry(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        d = ApplicationsApplet(48, config=Config())
        menu = d._build_launcher_menu()
        items = menu.get_children()
        assert items[0]._child.children[0].placeholder == "Search applications..."
        assert items[0].reserve_indicator is True
        assert isinstance(menu, applications_applet_mod.Gtk.Menu)

    def test_right_click_menu_items_are_empty(self):
        d = ApplicationsApplet(48, config=Config())
        assert d.get_menu_items() == []

    def test_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            d = ApplicationsApplet(size, config=Config())
            pixbuf = d.create_icon(size)
            assert pixbuf is not None

    def test_search_shows_direct_results_without_losing_focus(self, monkeypatch):
        self._fake_gtk(monkeypatch)

        firefox = MagicMock()
        firefox.get_display_name.return_value = "Firefox"
        firefox.get_icon.return_value = None
        chrome = MagicMock()
        chrome.get_display_name.return_value = "Google Chrome"
        chrome.get_icon.return_value = None
        writer = MagicMock()
        writer.get_display_name.return_value = "LibreOffice Writer"
        writer.get_icon.return_value = None
        monkeypatch.setattr(
            applications_applet_mod,
            "_build_app_categories",
            lambda: {
                "Internet": [firefox, chrome],
                "Office": [writer],
            },
        )
        launch = MagicMock()
        monkeypatch.setattr(applications_applet_mod, "_launch_app", launch)

        applet = ApplicationsApplet(48, config=Config())
        menu = applet._build_launcher_menu()
        items = menu.get_children()
        search_entry = items[0]._child.children[0]
        internet_item = items[2]
        office_item = items[3]

        search_entry.emit("map")
        assert search_entry.focused is True
        assert [
            child.get_label() for child in internet_item.get_submenu().get_children()
        ] == [
            "Firefox",
            "Google Chrome",
        ]
        assert [
            child.get_label() for child in office_item.get_submenu().get_children()
        ] == [
            "LibreOffice Writer",
        ]
        assert internet_item.get_submenu().shown is True
        assert office_item.get_submenu().shown is True

        search_entry.set_text("fire")
        search_entry.emit("changed")

        assert internet_item.visible is False
        assert office_item.visible is False
        assert search_entry.focused is True
        assert len(menu.get_children()) == 5
        result = menu.get_children()[-1]
        assert result.get_label() == "Firefox"
        assert result.get_submenu() is None

        callback, args = result._signals["activate"][0]
        callback(result, *args)
        launch.assert_called_once_with(app_info=firefox)

        search_entry.set_text("writer")
        search_entry.emit("changed")

        assert internet_item.visible is False
        assert office_item.visible is False
        assert search_entry.focused is True
        assert len(menu.get_children()) == 5
        assert menu.get_children()[-1].get_label() == "LibreOffice Writer"

        search_entry.set_text("")
        search_entry.emit("changed")

        assert internet_item.visible is True
        assert office_item.visible is True
        assert search_entry.focused is True
        assert len(menu.get_children()) == 4
        assert len(internet_item.get_submenu().get_children()) == 2

    def test_application_rows_drag_desktop_uri_to_dock(self, tmp_path, monkeypatch):
        self._fake_gtk(monkeypatch)
        desktop_file = tmp_path / "org.example.Tool.desktop"
        desktop_file.write_text("[Desktop Entry]\nType=Application\n", encoding="utf-8")
        app = ApplicationEntry(
            desktop_id="org.example.Tool.desktop",
            name="Example Tool",
            categories="Utility;",
            icon_name="",
        )
        monkeypatch.setattr(
            apps_shared.desktop_entries,
            "find_desktop_file",
            lambda _desktop_id: desktop_file,
        )
        submenu = applications_applet_mod.Gtk.Menu()

        applications_applet_mod._populate_app_submenu(
            submenu=submenu,
            apps=[app],
            config=None,
        )

        menu_item = submenu.get_children()[0]
        assert menu_item.drag_source_args is not None
        callback, args = menu_item._signals["drag-data-get"][0]
        selection = MagicMock()
        callback(menu_item, None, selection, 0, 0, *args)
        selection.set_uris.assert_called_once_with([desktop_file.as_uri()])

    def test_application_drag_shows_entry_icon(self, tmp_path, monkeypatch):
        self._fake_gtk(monkeypatch)
        desktop_file = tmp_path / "org.example.Tool.desktop"
        desktop_file.write_text("[Desktop Entry]\nType=Application\n", encoding="utf-8")
        icon = MagicMock()
        gio_app_info = MagicMock()
        gio_app_info.get_filename.return_value = str(desktop_file)
        gio_app_info.get_icon.return_value = icon
        app = ApplicationEntry(
            desktop_id="org.example.Tool.desktop",
            name="Example Tool",
            categories="Utility;",
            icon_name="",
            app_info=gio_app_info,
        )
        submenu = applications_applet_mod.Gtk.Menu()
        set_drag_icon = MagicMock()
        monkeypatch.setattr(
            applications_applet_mod.Gtk,
            "drag_set_icon_gicon",
            set_drag_icon,
            raising=False,
        )

        applications_applet_mod._populate_app_submenu(
            submenu=submenu,
            apps=[app],
            config=None,
        )

        menu_item = submenu.get_children()[0]
        callback, args = menu_item._signals["drag-begin"][0]
        context = MagicMock()
        callback(menu_item, context, *args)

        set_drag_icon.assert_called_once_with(context, icon, 0, 0)

    def test_application_rows_are_not_draggable_when_icons_are_locked(
        self, tmp_path, monkeypatch
    ):
        self._fake_gtk(monkeypatch)
        desktop_file = tmp_path / "org.example.Tool.desktop"
        desktop_file.write_text("[Desktop Entry]\nType=Application\n", encoding="utf-8")
        app = ApplicationEntry(
            desktop_id="org.example.Tool.desktop",
            name="Example Tool",
            categories="Utility;",
            icon_name="",
        )
        monkeypatch.setattr(
            apps_shared.desktop_entries,
            "find_desktop_file",
            lambda _desktop_id: desktop_file,
        )
        submenu = applications_applet_mod.Gtk.Menu()

        applications_applet_mod._populate_app_submenu(
            submenu=submenu,
            apps=[app],
            config=SimpleNamespace(lock_icons=True),
        )

        assert submenu.get_children()[0].drag_source_args is None


class TestApplicationsRenderHelpers:
    def test_normalize_menu_icon_sets_consistent_metrics(self):
        image = MagicMock()

        applications_render_mod.normalize_menu_icon(image=image)

        image.set_pixel_size.assert_called_once_with(
            applications_render_mod.MENU_ICON_PX
        )
        image.set_size_request.assert_called_once_with(
            applications_render_mod.MENU_ICON_PX,
            applications_render_mod.MENU_ICON_PX,
        )
        image.set_valign.assert_called_once_with(
            applications_render_mod.Gtk.Align.CENTER
        )

    def test_make_menu_item_with_icon_supports_gicon_icon_name_and_text_only(self):
        class _FakeImage:
            def __init__(self):
                self.margin_start = 0
                self.margin_end = 0

            @staticmethod
            def new_from_gicon(_gicon, _size):
                return _FakeImage()

            @staticmethod
            def new_from_icon_name(_name, _size):
                return _FakeImage()

            def set_pixel_size(self, _value):
                return

            def set_size_request(self, *_args):
                return

            def set_valign(self, _value):
                return

            def set_margin_start(self, value):
                self.margin_start = value

            def set_margin_end(self, value):
                self.margin_end = value

        class _FakeLabel:
            def __init__(self, label=""):
                self.label = label

            def set_xalign(self, _value):
                return

            def set_margin_start(self, _value):
                return

        class _FakeBox:
            def __init__(self, **_kwargs):
                self.children = []

            def set_halign(self, _value):
                return

            def set_margin_start(self, _value):
                return

            def set_margin_end(self, _value):
                return

            def pack_start(self, child, *_args):
                self.children.append(child)

            def get_children(self):
                return list(self.children)

        class _FakeMenuItem:
            def __init__(self):
                self.child = None

            def add(self, child):
                self.child = child

            def get_child(self):
                return self.child

        original_gtk = applications_render_mod.Gtk
        applications_render_mod.Gtk = SimpleNamespace(  # type: ignore[assignment]
            MenuItem=_FakeMenuItem,
            Box=_FakeBox,
            Image=_FakeImage,
            Label=_FakeLabel,
            Orientation=SimpleNamespace(HORIZONTAL=0),
            Align=SimpleNamespace(START=0, CENTER=1),
            IconSize=SimpleNamespace(MENU=1),
        )

        try:
            item = applications_render_mod.make_menu_item_with_icon(
                label="Calculator",
                gicon=applications_render_mod.Gio.ThemedIcon.new(
                    "accessories-calculator"
                ),
            )
            row = item.get_child()
            assert row is not None
            assert len(row.get_children()) == 2

            icon_name_item = applications_render_mod.make_menu_item_with_icon(
                label="Weather",
                icon_name="weather-clear",
            )
            icon_name_row = icon_name_item.get_child()
            assert icon_name_row is not None
            assert len(icon_name_row.get_children()) == 2

            text_only_item = applications_render_mod.make_menu_item_with_icon(
                label="Plain"
            )
            text_only_row = text_only_item.get_child()
            assert text_only_row is not None
            assert len(text_only_row.get_children()) == 1
        finally:
            applications_render_mod.Gtk = original_gtk  # type: ignore[assignment]
