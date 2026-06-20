"""Integration-style tests for DnDHandler."""

from __future__ import annotations

import stat
import sys
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock

try:
    import gi  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

import docking.ui.dnd as dnd_mod
from docking.core.items import APP_KIND, APPLET_KIND, FILE_KIND, FOLDER_KIND
from docking.core.position import Position
from docking.platform.model import DockItem
from docking.ui.geometry import Rect


def _frame(*, item_index: int = -1, insert_index: int = 0, count: int = 1):
    item_geometries = [
        SimpleNamespace(
            item=DockItem(desktop_id=f"item{i}.desktop", kind=APP_KIND),
            draw_rect=Rect(i * 70, 0, 48, 48),
        )
        for i in range(count)
    ]
    return SimpleNamespace(
        cursor_rect=Rect(0, 0, 400, 60),
        item_geometries=item_geometries,
        item_index_at_point=MagicMock(return_value=item_index),
        item_at_point=MagicMock(return_value=None),
        insertion_index_for_main=MagicMock(return_value=insert_index),
    )


def _make_handler(monkeypatch, lock_icons: bool = False):
    drawing_area = MagicMock()
    default_frame = _frame()

    model = MagicMock()
    config = SimpleNamespace(
        lock_icons=lock_icons,
        pos=Position.BOTTOM,
        icon_size=48,
        zoom_percent=2.0,
        scaled_icon_size=96,
        pinned=[],
        save=MagicMock(),
    )
    renderer = SimpleNamespace(slide_offsets={}, prev_positions={})
    theme = SimpleNamespace(item_padding=8, horizontal_padding=10)
    launcher = MagicMock()
    autohide = SimpleNamespace(
        enabled=True,
        set_disabled=MagicMock(),
        set_hovered=MagicMock(),
        on_mouse_enter=MagicMock(),
        on_mouse_leave=MagicMock(),
    )
    pointer = MagicMock()
    pointer.get_position.return_value = (None, 0, 0)
    seat = MagicMock()
    seat.get_pointer.return_value = pointer
    display = MagicMock()
    display.get_default_seat.return_value = seat
    window = SimpleNamespace(
        cursor_x=20.0,
        cursor_y=8.0,
        autohide=autohide,
        close_open_folder_stack_for_item=MagicMock(),
        is_pointer_inside_dock=MagicMock(return_value=False),
        get_display=MagicMock(return_value=display),
        get_position=MagicMock(return_value=(0, 0)),
        get_size=MagicMock(return_value=(400, 60)),
    )
    monkeypatch.setattr(dnd_mod, "show_poof", MagicMock())
    return dnd_mod.DnDHandler(
        drawing_area,
        window,
        model,
        config,
        renderer,
        theme,
        launcher,
        geometry_builder=SimpleNamespace(build_frame=lambda **_kwargs: default_frame),
    )


class _FakeResponseDialog:
    def __init__(self, response: int) -> None:
        self.response = response
        self.destroyed = False
        self.secondary_text = ""
        self.buttons: list[tuple[str, int]] = []

    def format_secondary_text(self, text: str) -> None:
        self.secondary_text = text

    def add_button(self, label: str, response: int) -> None:
        self.buttons.append((label, response))

    def set_default_response(self, _response: int) -> None:
        pass

    def run(self) -> int:
        return self.response

    def destroy(self) -> None:
        self.destroyed = True


class TestSetupAndToggle:
    def test_setup_enables_source_and_dest_when_unlocked(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch, lock_icons=False)
        da = handler._drawing_area
        # Then
        # When
        da.drag_source_set.assert_called_once()
        da.drag_dest_set.assert_called_once()
        # drag handlers are always connected
        assert da.connect.call_count >= 6

    def test_set_locked_toggles_dnd(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch)
        handler._disable_dnd = MagicMock()
        handler._enable_dnd = MagicMock()
        # When
        handler.set_locked(True)
        handler.set_locked(False)
        # Then
        handler._disable_dnd.assert_called_once()
        handler._enable_dnd.assert_called_once()


class TestDragBeginMotion:
    def test_drag_begin_sets_drag_index_and_icon(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch)
        icon = MagicMock()
        icon.scale_simple.return_value = object()
        handler._model.visible_items.return_value = [
            DockItem(desktop_id="firefox.desktop", name="Firefox", icon=icon)
        ]
        handler._geometry_builder = SimpleNamespace(
            build_frame=lambda **_kwargs: _frame(item_index=0, count=1)
        )
        icon_set = MagicMock()
        monkeypatch.setattr(dnd_mod.Gtk, "drag_set_icon_pixbuf", icon_set)

        # When
        handler._on_drag_begin(handler._drawing_area, MagicMock())
        # Then
        assert handler._drag_from == 0
        assert handler.drag_index == 0
        icon_set.assert_called_once()

    def test_drag_motion_external_updates_insert_gap(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch)
        handler._drag_from = -1
        handler.drop_insert_index = -1
        handler._model.visible_items.return_value = [
            DockItem("a.desktop"),
            DockItem("b.desktop"),
        ]
        rest_frame = _frame(insert_index=0, count=2)
        handler._geometry_builder = SimpleNamespace(
            build_frame=lambda **_kwargs: rest_frame
        )
        status_calls = []
        monkeypatch.setattr(
            dnd_mod.Gdk,
            "drag_status",
            lambda _ctx, action, _time: status_calls.append(action),
        )
        widget = handler._drawing_area

        # When
        handled = handler._on_drag_motion(widget, MagicMock(), x=20, y=5, time=1)
        # Then
        assert handled is True
        assert handler.drop_insert_index == 0
        handler._window.autohide.set_disabled.assert_called_once_with(
            True, reason="drag-motion"
        )
        handler._window.autohide.on_mouse_enter.assert_called_once()
        assert status_calls

    def test_drag_motion_external_clears_launcher_target_in_gap(self, monkeypatch):
        handler = _make_handler(monkeypatch)
        handler._drag_from = -1
        handler.drop_target_id = "left.desktop"
        left = DockItem("left.desktop", kind=APP_KIND)
        right = DockItem("right.desktop", kind=APP_KIND)
        rest_frame = SimpleNamespace(
            cursor_rect=Rect(0, 0, 400, 60),
            item_geometries=(),
            insertion_index_for_main=MagicMock(return_value=1),
        )
        gap_frame = SimpleNamespace(
            cursor_rect=Rect(0, 0, 400, 60),
            item_geometries=(
                SimpleNamespace(item=left, draw_rect=Rect(0, 0, 48, 48)),
                SimpleNamespace(item=right, draw_rect=Rect(70, 0, 48, 48)),
            ),
        )

        def build_frame(**kwargs):
            if kwargs.get("drop_insert_index", -1) >= 0:
                return gap_frame
            return rest_frame

        handler._geometry_builder = SimpleNamespace(build_frame=build_frame)
        monkeypatch.setattr(dnd_mod.Gdk, "drag_status", lambda *_a, **_k: None)

        handled = handler._on_drag_motion(
            handler._drawing_area,
            MagicMock(),
            x=80,
            y=10,
            time=1,
        )

        assert handled is True
        assert handler.drop_insert_index == 1
        assert handler.drop_target_id == ""

    def test_drag_motion_external_marks_drop_aware_applet_target(self, monkeypatch):
        handler = _make_handler(monkeypatch)
        handler._drag_from = -1
        applet_item = DockItem("applet://dragshare", kind=APPLET_KIND)
        applet = SimpleNamespace(accepts_drop_uris=MagicMock(return_value=True))
        handler._model.get_applet.return_value = applet
        rest_frame = SimpleNamespace(
            cursor_rect=Rect(0, 0, 400, 60),
            item_geometries=(),
            insertion_index_for_main=MagicMock(return_value=-1),
        )
        target_frame = SimpleNamespace(
            cursor_rect=Rect(0, 0, 400, 60),
            item_geometries=(
                SimpleNamespace(item=applet_item, draw_rect=Rect(0, 0, 48, 48)),
            ),
        )

        def build_frame(**kwargs):
            if "drop_insert_index" in kwargs:
                return target_frame
            return rest_frame

        handler._geometry_builder = SimpleNamespace(build_frame=build_frame)
        monkeypatch.setattr(dnd_mod.Gdk, "drag_status", lambda *_a, **_k: None)

        handled = handler._on_drag_motion(
            handler._drawing_area,
            MagicMock(),
            x=10,
            y=10,
            time=1,
        )

        assert handled is True
        assert handler.drop_target_id == "applet://dragshare"

    def test_drag_motion_internal_reorders(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch)
        handler._drag_from = 0
        handler.drag_index = 0
        handler._model.visible_items.return_value = [
            DockItem("a.desktop"),
            DockItem("b.desktop"),
        ]
        rest_frame = _frame(insert_index=2, count=2)
        handler._geometry_builder = SimpleNamespace(
            build_frame=lambda **_kwargs: rest_frame
        )
        monkeypatch.setattr(dnd_mod.Gdk, "drag_status", lambda *_a, **_k: None)

        # When
        handled = handler._on_drag_motion(handler._drawing_area, MagicMock(), 200, 5, 1)
        # Then
        assert handled is True
        handler._model.reorder_visible.assert_called_once()
        assert handler.drag_index == 1


class TestDropAndReceive:
    def test_drag_drop_requests_target_data(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch)
        widget = handler._drawing_area
        widget.drag_dest_find_target.return_value = "text/uri-list"

        # When
        handled = handler._on_drag_drop(widget, MagicMock(), 0, 0, 7)
        # Then
        assert handled is True
        widget.drag_get_data.assert_called_once()

    def test_drag_drop_without_target_clears_gap(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch)
        handler.drop_insert_index = 2
        widget = handler._drawing_area
        widget.drag_dest_find_target.return_value = None

        # When
        handled = handler._on_drag_drop(widget, MagicMock(), 0, 0, 7)
        # Then
        assert handled is False
        assert handler.drop_insert_index == -1
        widget.queue_draw.assert_called_once()

    def test_drag_data_received_internal_finishes_immediately(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch)
        handler._drag_from = 0
        finish = MagicMock()
        monkeypatch.setattr(dnd_mod.Gtk, "drag_finish", finish)

        # When
        handler._on_drag_data_received(
            handler._drawing_area,
            MagicMock(),
            0,
            0,
            MagicMock(),
            0,
            123,
        )
        # Then
        handler._window.autohide.set_hovered.assert_called_once_with(False)
        handler._window.autohide.set_disabled.assert_called_once_with(
            False, reason="drag-data-received-outside"
        )
        handler._window.autohide.on_mouse_leave.assert_called_once()
        finish.assert_called_once_with(ANY, True, False, 123)

    def test_drag_data_received_external_adds_pinned_item(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch)
        handler._drag_from = -1
        handler._drop_committed = True
        handler.drop_insert_index = 0
        handler._renderer.slide_offsets = {"firefox.desktop": 12.0}
        handler._renderer.prev_positions = {"firefox.desktop": 320.0}
        handler._model.pinned_items = []
        handler._model.find_by_desktop_id.return_value = None
        resolved = SimpleNamespace(
            name="Firefox",
            icon_name="firefox",
            wm_class="firefox",
        )
        handler._launcher.resolve.return_value = resolved
        handler._launcher.load_icon.return_value = object()
        selection = MagicMock()
        selection.get_uris.return_value = [
            "file:///usr/share/applications/firefox.desktop"
        ]
        finish = MagicMock()
        monkeypatch.setattr(dnd_mod.Gtk, "drag_finish", finish)

        # When
        handler._on_drag_data_received(
            handler._drawing_area,
            MagicMock(),
            0,
            0,
            selection,
            1,
            77,
        )
        # Then
        assert [entry.target for entry in handler._config.pinned] == ["firefox.desktop"]
        assert len(handler._model.pinned_items) == 1
        handler._config.save.assert_called_once()
        handler._model.sync_pinned_to_config.assert_called_once()
        assert handler._renderer.slide_offsets == {}
        assert handler._renderer.prev_positions == {}
        handler._model.notify.assert_called_once()
        handler._window.autohide.set_hovered.assert_called_once_with(False)
        handler._window.autohide.set_disabled.assert_called_once_with(
            False, reason="drag-data-received-outside"
        )
        handler._window.autohide.on_mouse_leave.assert_called_once()
        finish.assert_called_once_with(ANY, True, False, 77)

    def test_drag_data_received_between_apps_inserts_instead_of_launching(
        self, monkeypatch, tmp_path
    ):
        handler = _make_handler(monkeypatch)
        handler._drag_from = -1
        handler._drop_committed = True
        handler.drop_insert_index = 1
        handler._model.pinned_items = []
        handler._model.find_by_desktop_id.return_value = None
        file_uri = (tmp_path / "notes.txt").as_uri()
        handler._launcher.resolve_file.return_value = SimpleNamespace(
            target=file_uri,
            name="notes.txt",
            icon_name="text-x-generic",
            icon=object(),
            is_dir=False,
        )
        left = DockItem("left.desktop", kind=APP_KIND)
        right = DockItem("right.desktop", kind=APP_KIND)
        gap_frame = SimpleNamespace(
            cursor_rect=Rect(0, 0, 400, 60),
            item_geometries=(
                SimpleNamespace(item=left, draw_rect=Rect(0, 0, 48, 48)),
                SimpleNamespace(item=right, draw_rect=Rect(70, 0, 48, 48)),
            ),
            insertion_index_for_main=MagicMock(return_value=1),
        )
        handler._geometry_builder = SimpleNamespace(
            build_frame=lambda **_kwargs: gap_frame
        )
        desktop_app_info = MagicMock()
        desktop_app_info.launch_uris = MagicMock()
        monkeypatch.setattr(
            dnd_mod.Gio.DesktopAppInfo,
            "new",
            MagicMock(return_value=desktop_app_info),
        )
        selection = MagicMock()
        selection.get_uris.return_value = [file_uri]
        finish = MagicMock()
        monkeypatch.setattr(dnd_mod.Gtk, "drag_finish", finish)

        handler._on_drag_data_received(
            handler._drawing_area,
            MagicMock(),
            80,
            10,
            selection,
            1,
            77,
        )

        desktop_app_info.launch_uris.assert_not_called()
        assert [entry.target for entry in handler._config.pinned] == [file_uri]
        assert len(handler._model.pinned_items) == 1
        finish.assert_called_once_with(ANY, True, False, 77)

    def test_drag_data_received_on_applet_dispatches_drop(self, monkeypatch, tmp_path):
        handler = _make_handler(monkeypatch)
        handler._drag_from = -1
        handler.drop_insert_index = -1
        file_uri = (tmp_path / "notes.txt").as_uri()
        applet_item = DockItem("applet://dragshare", kind=APPLET_KIND)
        applet = SimpleNamespace(
            accepts_drop_uris=MagicMock(return_value=True),
            on_drop_uris=MagicMock(return_value=True),
        )
        handler._model.get_applet.return_value = applet
        handler._geometry_builder = SimpleNamespace(
            build_frame=lambda **_kwargs: SimpleNamespace(
                cursor_rect=Rect(0, 0, 400, 60),
                item_geometries=(
                    SimpleNamespace(item=applet_item, draw_rect=Rect(0, 0, 48, 48)),
                ),
            )
        )
        selection = MagicMock()
        selection.get_uris.return_value = [file_uri]
        finish = MagicMock()
        monkeypatch.setattr(dnd_mod.Gtk, "drag_finish", finish)

        handler._on_drag_data_received(
            handler._drawing_area,
            MagicMock(),
            10,
            10,
            selection,
            1,
            77,
        )

        applet.on_drop_uris.assert_called_once_with([file_uri])
        handler._model.find_by_desktop_id.assert_not_called()
        finish.assert_called_once_with(ANY, True, False, 77)

    def test_drag_data_received_on_applet_rejection_does_not_pin(
        self, monkeypatch, tmp_path
    ):
        handler = _make_handler(monkeypatch)
        handler._drag_from = -1
        handler.drop_insert_index = -1
        file_uri = (tmp_path / "notes.txt").as_uri()
        applet_item = DockItem("applet://other", kind=APPLET_KIND)
        applet = SimpleNamespace(accepts_drop_uris=MagicMock(return_value=False))
        handler._model.get_applet.return_value = applet
        handler._geometry_builder = SimpleNamespace(
            build_frame=lambda **_kwargs: SimpleNamespace(
                cursor_rect=Rect(0, 0, 400, 60),
                item_geometries=(
                    SimpleNamespace(item=applet_item, draw_rect=Rect(0, 0, 48, 48)),
                ),
            )
        )
        selection = MagicMock()
        selection.get_uris.return_value = [file_uri]
        finish = MagicMock()
        monkeypatch.setattr(dnd_mod.Gtk, "drag_finish", finish)

        handler._on_drag_data_received(
            handler._drawing_area,
            MagicMock(),
            10,
            10,
            selection,
            1,
            77,
        )

        handler._model.find_by_desktop_id.assert_not_called()
        finish.assert_called_once_with(ANY, False, False, 77)

    def test_item_from_uri_builds_folder_item(self, monkeypatch, tmp_path):
        handler = _make_handler(monkeypatch)
        folder_uri = tmp_path.as_uri()
        handler._launcher.resolve_file.return_value = SimpleNamespace(
            target=folder_uri,
            name=tmp_path.name,
            icon_name="folder",
            icon=object(),
            is_dir=True,
        )

        item = handler._item_from_uri(folder_uri)

        assert item is not None
        assert item.kind == FOLDER_KIND
        assert item.target == folder_uri

    def test_item_from_uri_builds_file_item(self, monkeypatch, tmp_path):
        handler = _make_handler(monkeypatch)
        file_uri = (tmp_path / "notes.txt").as_uri()
        handler._launcher.resolve_file.return_value = SimpleNamespace(
            target=file_uri,
            name="notes.txt",
            icon_name="text-x-generic",
            icon=object(),
            is_dir=False,
        )

        item = handler._item_from_uri(file_uri)

        assert item is not None
        assert item.kind == FILE_KIND
        assert item.target == file_uri

    def test_item_from_uri_builds_generated_launcher_for_executable(
        self, monkeypatch, tmp_path
    ):
        handler = _make_handler(monkeypatch)
        binary = tmp_path / "tool"
        generated = dnd_mod.desktop_entries.GeneratedDesktopEntry(
            desktop_id="docking-generated-tool-123.desktop",
            path=tmp_path / "docking-generated-tool-123.desktop",
            name="tool",
            icon_name="application-x-executable",
        )
        resolved = SimpleNamespace(
            name="tool",
            icon_name="application-x-executable",
            wm_class="tool",
        )
        monkeypatch.setattr(
            dnd_mod.desktop_entries,
            "create_desktop_entry_for_executable",
            MagicMock(return_value=generated),
        )
        handler._launcher.resolve.return_value = resolved
        icon = object()
        handler._launcher.load_desktop_icon.return_value = icon

        item = handler._item_from_uri(binary.as_uri())

        assert item is not None
        assert item.kind == APP_KIND
        assert item.desktop_id == generated.desktop_id
        assert item.target == generated.desktop_id
        assert item.name == "tool"
        assert item.icon is icon
        handler._launcher.refresh_desktop_entries.assert_called_once_with()
        handler._launcher.resolve.assert_called_once_with(generated.desktop_id)

    def test_item_from_uri_confirms_chmod_for_non_executable_appimage(
        self, monkeypatch, tmp_path
    ):
        handler = _make_handler(monkeypatch)
        appimage = tmp_path / "GIMP-3.2.4-x86_64.AppImage"
        appimage.write_text("#!/bin/sh\n", encoding="utf-8")
        appimage.chmod(0o644)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setattr(
            dnd_mod.desktop_entries, "_refresh_desktop_database", lambda _d: None
        )
        dialog = _FakeResponseDialog(dnd_mod.Gtk.ResponseType.OK)
        monkeypatch.setattr(
            dnd_mod.Gtk,
            "MessageDialog",
            lambda **_kwargs: dialog,
        )
        handler._launcher.resolve.return_value = SimpleNamespace(
            name="GIMP 3.2.4 x86 64",
            icon_name="application-x-appimage",
            wm_class="GIMP-3.2.4-x86_64",
        )

        item = handler._item_from_uri(appimage.as_uri())

        assert item is not None
        assert item.kind == APP_KIND
        assert item.desktop_id.startswith("docking-generated-gimp-3-2-4-x86-64-")
        assert appimage.stat().st_mode & stat.S_IXUSR
        assert dialog.destroyed
        handler._launcher.refresh_desktop_entries.assert_called_once_with()

    def test_item_from_uri_cancelled_appimage_permission_does_not_pin_file(
        self, monkeypatch, tmp_path
    ):
        handler = _make_handler(monkeypatch)
        appimage = tmp_path / "GIMP.AppImage"
        appimage.write_text("#!/bin/sh\n", encoding="utf-8")
        appimage.chmod(0o644)
        dialog = _FakeResponseDialog(dnd_mod.Gtk.ResponseType.CANCEL)
        monkeypatch.setattr(
            dnd_mod.Gtk,
            "MessageDialog",
            lambda **_kwargs: dialog,
        )

        item = handler._item_from_uri(appimage.as_uri())

        assert item is None
        assert not (appimage.stat().st_mode & stat.S_IXUSR)
        handler._launcher.resolve_file.assert_not_called()
        handler._launcher.refresh_desktop_entries.assert_not_called()

    def test_drag_data_received_pins_generated_launcher_for_executable(
        self, monkeypatch, tmp_path
    ):
        handler = _make_handler(monkeypatch)
        handler._drag_from = -1
        handler._drop_committed = True
        handler.drop_insert_index = 0
        handler._model.pinned_items = []
        handler._model.find_by_desktop_id.return_value = None
        generated = dnd_mod.desktop_entries.GeneratedDesktopEntry(
            desktop_id="docking-generated-tool-123.desktop",
            path=tmp_path / "docking-generated-tool-123.desktop",
            name="tool",
            icon_name="application-x-executable",
        )
        resolved = SimpleNamespace(
            name="tool",
            icon_name="application-x-executable",
            wm_class="tool",
        )
        monkeypatch.setattr(
            dnd_mod.desktop_entries,
            "create_desktop_entry_for_executable",
            MagicMock(return_value=generated),
        )
        handler._launcher.resolve.return_value = resolved
        selection = MagicMock()
        selection.get_uris.return_value = [(tmp_path / "tool").as_uri()]
        finish = MagicMock()
        monkeypatch.setattr(dnd_mod.Gtk, "drag_finish", finish)

        handler._on_drag_data_received(
            handler._drawing_area,
            MagicMock(),
            0,
            0,
            selection,
            1,
            77,
        )

        assert [entry.kind for entry in handler._config.pinned] == [APP_KIND]
        assert [entry.target for entry in handler._config.pinned] == [
            generated.desktop_id
        ]
        assert [item.kind for item in handler._model.pinned_items] == [APP_KIND]
        assert [item.desktop_id for item in handler._model.pinned_items] == [
            generated.desktop_id
        ]
        handler._config.save.assert_called_once()
        handler._model.sync_pinned_to_config.assert_called_once()
        handler._model.notify.assert_called_once()
        finish.assert_called_once_with(ANY, True, False, 77)

    def test_drag_data_received_does_not_duplicate_generated_launcher(
        self, monkeypatch, tmp_path
    ):
        handler = _make_handler(monkeypatch)
        handler._drag_from = -1
        handler._drop_committed = True
        handler.drop_insert_index = 0
        handler._model.pinned_items = []
        generated = dnd_mod.desktop_entries.GeneratedDesktopEntry(
            desktop_id="docking-generated-tool-123.desktop",
            path=tmp_path / "docking-generated-tool-123.desktop",
            name="tool",
            icon_name="application-x-executable",
        )
        resolved = SimpleNamespace(
            name="tool",
            icon_name="application-x-executable",
            wm_class="tool",
        )
        monkeypatch.setattr(
            dnd_mod.desktop_entries,
            "create_desktop_entry_for_executable",
            MagicMock(return_value=generated),
        )
        handler._launcher.resolve.return_value = resolved
        handler._model.find_by_desktop_id.return_value = DockItem(generated.desktop_id)
        selection = MagicMock()
        selection.get_uris.return_value = [(tmp_path / "tool").as_uri()]
        finish = MagicMock()
        monkeypatch.setattr(dnd_mod.Gtk, "drag_finish", finish)

        handler._on_drag_data_received(
            handler._drawing_area,
            MagicMock(),
            0,
            0,
            selection,
            1,
            77,
        )

        assert handler._config.pinned == []
        assert handler._model.pinned_items == []
        finish.assert_called_once_with(ANY, False, False, 77)


class TestDragLeaveEnd:
    def test_drag_leave_schedules_deferred_clear_without_releasing_autohide(
        self, monkeypatch
    ):
        # Given
        handler = _make_handler(monkeypatch)
        handler._drag_from = -1
        handler.drop_insert_index = 1
        timeout_calls = []
        monkeypatch.setattr(
            dnd_mod.GLib,
            "timeout_add",
            lambda delay, cb, widget: timeout_calls.append((delay, cb, widget)) or 1,
        )
        widget = handler._drawing_area

        # When
        handler._on_drag_leave(widget, MagicMock(), 0)
        # Then
        assert timeout_calls and timeout_calls[0][0] == 100
        handler._window.autohide.set_hovered.assert_not_called()
        widget.queue_draw.assert_called()

    def test_deferred_clear_drop_gap_releases_autohide_when_still_outside(
        self, monkeypatch
    ):
        # Given
        handler = _make_handler(monkeypatch)
        handler._drag_from = -1
        handler.drop_insert_index = 2
        widget = handler._drawing_area
        # Then
        # When
        assert handler._deferred_clear_drop_gap(widget) is False
        assert handler.drop_insert_index == -1
        handler._window.autohide.set_hovered.assert_called_once_with(False)
        handler._window.autohide.set_disabled.assert_called_once_with(
            False, reason="drag-leave-outside"
        )
        handler._window.autohide.on_mouse_leave.assert_called_once()
        widget.queue_draw.assert_called_once()

    def test_drag_end_unpins_when_dropped_outside(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch)
        handler._drag_from = 0
        handler.drag_index = 0
        pinned = DockItem(desktop_id="firefox.desktop", is_pinned=True, name="Firefox")
        handler._model.visible_items.return_value = [pinned]
        pointer = handler._window.get_display.return_value.get_default_seat.return_value
        pointer.get_pointer.return_value.get_position.return_value = (None, 200, 50)
        handler._window.get_position.return_value = (100, 200)
        handler._window.get_size.return_value = (400, 60)
        widget = handler._drawing_area

        # When
        handler._on_drag_end(widget, MagicMock())
        # Then
        handler._model.unpin_item.assert_called_once_with("firefox.desktop")
        assert handler._renderer.slide_offsets == {}
        assert handler._renderer.prev_positions == {}
        handler._config.save.assert_called()
        handler._window.autohide.set_hovered.assert_called_once_with(False)
        handler._window.autohide.set_disabled.assert_called_once_with(
            False, reason="drag-end-outside"
        )
        handler._window.autohide.on_mouse_leave.assert_called_once()
        widget.queue_draw.assert_called()

    def test_drag_end_closes_open_folder_stack_when_folder_unpinned(self, monkeypatch):
        handler = _make_handler(monkeypatch)
        handler._drag_from = 0
        handler.drag_index = 0
        folder = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            is_pinned=True,
            name="Docs",
        )
        handler._model.visible_items.return_value = [folder]
        pointer = handler._window.get_display.return_value.get_default_seat.return_value
        pointer.get_pointer.return_value.get_position.return_value = (None, 200, 50)
        handler._window.get_position.return_value = (100, 200)
        handler._window.get_size.return_value = (400, 60)

        handler._on_drag_end(handler._drawing_area, MagicMock())

        handler._window.close_open_folder_stack_for_item.assert_called_once_with(
            folder.desktop_id
        )
        handler._model.unpin_item.assert_called_once_with(folder.desktop_id)
