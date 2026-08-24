"""Tests for the Applications applet."""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.applets.applications.applet as applications_applet_mod
import docking.applets.applications.render as applications_render_mod
from docking.applets.applications.applet import ApplicationsApplet
from docking.applets.applications.state import build_app_categories
from docking.core.config import Config
from docking.platform.applications.registry import UnidentifiedApplicationListing
from docking.platform.applications.types import (
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
)


def _application(
    desktop_id: str,
    name: str,
    *,
    categories: str = "",
    icon: str = "",
    desktop_file: Path | None = None,
    exec_line: str = "example",
    visible: bool = True,
    has_gio_source: bool = False,
) -> ApplicationInfo:
    return ApplicationInfo(
        desktop_id=desktop_id,
        name=name,
        declared_icon=icon,
        wm_class=desktop_id.removesuffix(".desktop"),
        exec_line=exec_line,
        origin=ApplicationOrigin.INSTALLED,
        location=ApplicationLocation.HOST,
        desktop_file=desktop_file,
        executable_path=None,
        aliases=(),
        visible=visible,
        has_gio_source=has_gio_source,
        categories=tuple(filter(None, categories.split(";"))),
        categories_raw=categories,
    )


class _Registry:
    def __init__(
        self,
        applications: tuple[ApplicationInfo, ...] = (),
        unidentified: tuple[UnidentifiedApplicationListing, ...] = (),
        *,
        handles: dict[str, object] | None = None,
    ) -> None:
        self.applications = applications
        self.unidentified = unidentified
        self.handles = handles or {}

    def snapshot(self) -> tuple[ApplicationInfo, ...]:
        return self.applications

    def unidentified_snapshot(
        self,
    ) -> tuple[UnidentifiedApplicationListing, ...]:
        return self.unidentified

    def _gio_handle_for(self, desktop_id: str) -> object | None:
        return self.handles.get(desktop_id)


def _make_applet(
    size: int = 48,
    *,
    registry: _Registry | None = None,
    launcher: object | None = None,
) -> ApplicationsApplet:
    return ApplicationsApplet(
        size,
        config=Config(),
        application_registry=registry or _Registry(),  # ty: ignore[invalid-argument-type]
        application_launcher=launcher or MagicMock(),  # ty: ignore[invalid-argument-type]
    )


class TestBuildAppCategories:
    def test_returns_dict(self):
        categories = build_app_categories(
            _Registry()  # ty: ignore[invalid-argument-type]
        )
        assert isinstance(categories, dict)
        assert categories == {}

    def test_uses_visible_and_idless_registry_snapshots_and_sorts_names(self):
        host = _application(
            "org.example.Host.desktop",
            "zeta Host Tool",
            categories="Development;IDE;",
        )
        file_only = _application(
            "org.example.File.desktop",
            "Alpha File Tool",
            categories="Development;",
        )
        idless = UnidentifiedApplicationListing(
            listing_key="opaque:7",
            name="Middle ID-less",
            categories="Development;",
            icon_name="",
            desktop_file=None,
        )
        registry = _Registry((host, file_only), (idless,))

        categories = build_app_categories(
            registry  # ty: ignore[invalid-argument-type]
        )

        assert list(categories) == ["Development"]
        assert [app.name for app in categories["Development"]] == [
            "Alpha File Tool",
            "Middle ID-less",
            "zeta Host Tool",
        ]

    def test_maps_unknown_category_to_other(self):
        registry = _Registry(
            (
                _application(
                    "org.example.Other.desktop",
                    "Other",
                    categories="X-Custom;",
                ),
            )
        )

        categories = build_app_categories(
            registry  # ty: ignore[invalid-argument-type]
        )

        assert list(categories) == ["Other"]


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
                self._signals: dict[str, list[Callable[[object], None]]] = {}

            def set_placeholder_text(self, text: str) -> None:
                self.placeholder = text

            def set_width_chars(self, value: int) -> None:
                self.width_chars = value

            def connect(
                self,
                signal: str,
                callback: Callable[[object], None],
            ) -> None:
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
        d = _make_applet()
        assert d.item.icon is not None

    def test_left_click_opens_launcher_menu(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        d = _make_applet()
        d.on_clicked()

        assert d._popup_menu is not None
        assert d._popup_menu.shown is True
        assert d._popup_menu.popup_event is None

    def test_launcher_menu_contains_search_entry(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        d = _make_applet()
        menu = d._build_launcher_menu()
        items = menu.get_children()
        assert items[0]._child.children[0].placeholder == "Search applications..."
        assert items[0].reserve_indicator is True
        assert isinstance(menu, applications_applet_mod.Gtk.Menu)

    def test_right_click_menu_items_are_empty(self):
        d = _make_applet()
        assert d.get_menu_items() == []

    def test_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            d = _make_applet(size)
            pixbuf = d.create_icon(size)
            assert pixbuf is not None

    def test_search_shows_direct_results_without_losing_focus(self, monkeypatch):
        self._fake_gtk(monkeypatch)

        firefox = _application(
            "org.mozilla.Firefox.desktop",
            "Firefox",
            categories="Network;",
        )
        chrome = _application(
            "com.google.Chrome.desktop",
            "Google Chrome",
            categories="Network;",
        )
        writer = _application(
            "org.libreoffice.Writer.desktop",
            "LibreOffice Writer",
            categories="Office;",
        )
        registry = _Registry((firefox, chrome, writer))
        launcher = MagicMock()
        launcher.launch.return_value = True

        applet = _make_applet(
            registry=registry,
            launcher=launcher,
        )
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
        launcher.launch.assert_called_once_with("org.mozilla.Firefox.desktop")

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
        app = _application(
            "org.example.Tool.desktop",
            "Example Tool",
            categories="Utility;",
            desktop_file=desktop_file,
        )
        submenu = applications_applet_mod.Gtk.Menu()

        applications_applet_mod._populate_app_submenu(
            submenu=submenu,
            apps=[app],
            config=SimpleNamespace(lock_icons=False),
            registry=MagicMock(),
            launcher=MagicMock(),
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
        gio_app_info.get_icon.return_value = icon
        app = _application(
            "org.example.Tool.desktop",
            "Example Tool",
            categories="Utility;",
            desktop_file=desktop_file,
            has_gio_source=True,
        )
        registry = _Registry(
            (app,),
            handles={"org.example.Tool.desktop": gio_app_info},
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
            config=SimpleNamespace(lock_icons=False),
            registry=registry,  # ty: ignore[invalid-argument-type]
            launcher=MagicMock(),
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
        app = _application(
            "org.example.Tool.desktop",
            "Example Tool",
            categories="Utility;",
            desktop_file=desktop_file,
        )
        submenu = applications_applet_mod.Gtk.Menu()

        applications_applet_mod._populate_app_submenu(
            submenu=submenu,
            apps=[app],
            config=SimpleNamespace(  # ty: ignore[invalid-argument-type]
                lock_icons=True
            ),
            registry=MagicMock(),
            launcher=MagicMock(),
        )

        assert submenu.get_children()[0].drag_source_args is None

    def test_idless_menu_row_launches_by_opaque_listing_key(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        app = UnidentifiedApplicationListing(
            listing_key="gio-idless:9",
            name="ID-less",
            categories="Utility;",
            icon_name="",
            desktop_file=None,
        )
        launcher = MagicMock()
        launcher.launch_listing.return_value = True
        item = applications_applet_mod._application_menu_item(
            app_info=app,
            config=SimpleNamespace(lock_icons=False),
            registry=MagicMock(),
            launcher=launcher,
        )

        callback, args = item._signals["activate"][0]
        callback(item, *args)

        launcher.launch_listing.assert_called_once_with("gio-idless:9")


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
