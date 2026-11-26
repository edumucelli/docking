"""Exercise source selection and the live Preferences import entry point."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import gi
import pytest

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from docking.core.config import Config, PinnedEntry
from docking.platform.dock_import import (
    DockImportSource,
    SourcePin,
    read_dock_import_sources,
)
from docking.ui import dock_import, settings


def _source(name, applet, **preferences):
    return DockImportSource(
        name,
        pins=[SourcePin("applet", applet, (applet,))],
        settings=preferences,
    )


@pytest.mark.skipif(not Gtk.init_check()[0], reason="GTK display required")
@pytest.mark.parametrize("accept", [True, False])
def test_review_requires_source_selection_and_respects_cancel(monkeypatch, accept):
    sources = [
        _source("Plank", "clock", position="top"),
        _source("Cairo-Dock", "trash", position="left"),
    ]

    def respond(dialog):
        combo = next(
            w
            for w in dialog.get_content_area().get_children()
            if isinstance(w, Gtk.ComboBoxText)
        )
        response = dialog.get_widget_for_response(Gtk.ResponseType.OK)
        assert not response.get_sensitive()
        combo.set_active(1)
        assert response.get_sensitive()
        return Gtk.ResponseType.OK if accept else Gtk.ResponseType.CANCEL

    monkeypatch.setattr(dock_import, "_read_sources", lambda _kind, _path: sources)
    # Avoid dispatching GLib sources left pending by other GUI tests.
    monkeypatch.setattr(Gtk.Dialog, "run", respond)
    result = dock_import.review_dock_import(None, Config(), MagicMock(), first_run=True)
    if accept:
        assert result is not None
        assert result.config.pinned == ["applet://trash"]
        assert result.config.position == "left"
    else:
        assert result is None


@pytest.mark.skipif(not Gtk.init_check()[0], reason="GTK display required")
def test_review_rechecks_monitors_when_user_confirms(monkeypatch):
    source = DockImportSource("Plank", monitor="DP-1", settings={"icon_size": 64})
    monkeypatch.setattr(dock_import, "_read_sources", lambda _kind, _path: [source])
    connected = True
    monkeypatch.setattr(
        dock_import,
        "monitor_connector",
        lambda _display, index: "DP-1" if connected and index == 0 else None,
    )
    reviews = []

    def respond(dialog):
        nonlocal connected
        area = dialog.get_content_area()
        scroller = next(
            w for w in area.get_children() if isinstance(w, Gtk.ScrolledWindow)
        )
        buffer = scroller.get_child().get_buffer()
        reviews.append(buffer.get_text(*buffer.get_bounds(), False))
        connected = False
        return Gtk.ResponseType.OK

    monkeypatch.setattr(Gtk.Dialog, "run", respond)
    review = dock_import.review_dock_import(
        None, Config(), MagicMock(), first_run=False, kind="plank"
    )

    assert len(reviews) == 2
    assert "Display: DP-1" in reviews[0]
    assert "The source display is unavailable." in reviews[1]
    assert review is not None
    assert review.config.monitor_connector is None
    assert review.config.icon_size == 64


@pytest.mark.skipif(not Gtk.init_check()[0], reason="GTK display required")
@pytest.mark.parametrize(
    ("kind", "source_name", "missing_message"),
    [
        (
            "plank",
            "Plank / Plank Reloaded",
            "No Plank or Plank Reloaded setup was found.",
        ),
        ("cairo-dock", "Cairo-Dock", "No Cairo-Dock setup was found."),
        (
            None,
            "Docking file (shared.json)",
            "Choose a valid Docking JSON configuration file.",
        ),
    ],
)
@pytest.mark.parametrize("available", [True, False])
def test_preferences_review_opens_selected_dock(
    tmp_path, monkeypatch, kind, source_name, missing_message, available
):
    file_path = None
    if kind is None:
        file_path = tmp_path / "shared.json"
        file_path.write_text(
            '{"pinned": ["applet://trash"]}' if available else "not JSON",
            encoding="utf-8",
        )
        read = MagicMock(
            side_effect=lambda kind, path: read_dock_import_sources(
                kind, file_path=path
            )
        )
    else:
        source = _source(source_name, "trash")
        read = MagicMock(return_value=[source] if available else [])
    monkeypatch.setattr(dock_import, "_read_sources", read)

    def respond(dialog):
        children = dialog.get_content_area().get_children()
        assert not any(isinstance(widget, Gtk.ComboBoxText) for widget in children)
        scroller = next(
            widget for widget in children if isinstance(widget, Gtk.ScrolledWindow)
        )
        buffer = scroller.get_child().get_buffer()
        text = buffer.get_text(*buffer.get_bounds(), False)
        response = dialog.get_widget_for_response(Gtk.ResponseType.OK)
        assert response.get_sensitive() is available
        if available:
            assert text.startswith(source_name)
            return Gtk.ResponseType.OK
        assert text == missing_message
        return Gtk.ResponseType.CANCEL

    monkeypatch.setattr(Gtk.Dialog, "run", respond)
    review = dock_import.review_dock_import(
        None, Config(), MagicMock(), first_run=False, kind=kind, file_path=file_path
    )
    read.assert_called_once_with(kind, file_path)
    if available:
        assert review is not None
        assert review.config.pinned == ["applet://trash"]
    else:
        assert review is None


@pytest.mark.parametrize("pin_fails", [False, True])
def test_preferences_import_saves_then_refreshes_live_config(
    tmp_path, monkeypatch, pin_fails
):
    path = tmp_path / "dock.json"
    shared = tmp_path / "shared.json"
    shared.write_text(
        '{"pinned": ["applet://trash"], "position": "left", "hide_delay_ms": 25}',
        encoding="utf-8",
    )
    existing = PinnedEntry("applet", "applet://clock")
    imported = PinnedEntry("applet", "applet://trash")
    Config(pinned=[existing]).save(path)
    config = Config.load(path)
    actions, model, tab, registry = (MagicMock() for _ in range(4))
    controller = settings.SettingsWindowController(
        parent=MagicMock(),
        actions=actions,
        model=model,
        config=config,
        application_registry=registry,
    )
    controller._window = MagicMock()
    changed = MagicMock()
    controller._bindings = [SimpleNamespace(config_attr="position", on_change=changed)]
    monkeypatch.setattr(controller, "_sync_widgets", MagicMock())
    monkeypatch.setattr(controller, "_rebuild_applet_tab", MagicMock())
    show_message = MagicMock()
    monkeypatch.setattr(controller, "_show_message", show_message)
    monkeypatch.setattr(settings, "choose_docking_config_file", lambda _parent: shared)
    monkeypatch.setattr(
        settings,
        "review_dock_import",
        lambda *_args, file_path, **_kwargs: dock_import.prepare_dock_import(
            read_dock_import_sources(file_path=file_path)[0],
            config,
            registry,
            connectors=set(),
            theme_gap=0,
            first_run=False,
        ),
    )

    def append(_pins):
        assert Config.load(path).position == config.position == "left"
        return [imported] if pin_fails else []

    model.append_imported_pins.side_effect = append

    controller._import_dock(tab, None)

    expected_pins = [existing] if pin_fails else [existing, imported]
    assert Config.load(path).pinned == config.pinned == expected_pins
    backup = Config.load(tmp_path / "dock.json.bak")
    assert backup.pinned == [existing]
    assert backup.position == "bottom"
    model.append_imported_pins.assert_called_once_with([imported])
    if pin_fails:
        show_message.assert_called_once_with(
            controller._window,
            Gtk.MessageType.WARNING,
            "Some imported pins could not be loaded.",
            "Trash",
        )
    else:
        show_message.assert_not_called()
    changed.assert_called_once_with()
    actions.on_hide_mode_changed.assert_called_once_with()
