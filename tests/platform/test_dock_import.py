"""Plank and Cairo-Dock migration using the formats written by each dock."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace

import pytest
from gi.repository import GLib

from docking.core.config import Config, PinnedEntry
from docking.platform import dock_import
from docking.platform.dock_import import prepare_dock_import

pytestmark = pytest.mark.skipif(
    not dock_import.dock_import_supported(), reason="Unix dock profiles required"
)


def _write(root: Path, name: str, contents: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def _registry():
    apps = {
        desktop_id: SimpleNamespace(
            desktop_id=desktop_id, name=desktop_id.removesuffix(".desktop")
        )
        for desktop_id in ("browser.desktop", "editor.desktop", "vendor-viewer.desktop")
    }

    def by_path(path):
        return (
            apps["vendor-viewer.desktop"]
            if path == Path("/usr/share/applications/vendor/viewer.desktop")
            else None
        )

    return SimpleNamespace(get=apps.get, resolve_by_desktop_file=by_path)


def _prepare(source, base=None, *, first_run=True):
    return prepare_dock_import(
        source,
        base or Config(),
        _registry(),
        connectors={"DP-1"},
        theme_gap=4,
        first_run=first_run,
    )


def test_docking_file_import_adds_available_pins_and_portable_settings(
    tmp_path, monkeypatch
):
    picture = tmp_path / "picture.png"
    picture.touch()
    imported_config = Config(
        pinned=[
            PinnedEntry("app", "browser.desktop"),
            PinnedEntry("app", "missing.desktop"),
            PinnedEntry("file", picture.as_uri()),
        ],
        theme="exported-theme",
        transparency=0.7,
        anchor_applets=True,
        monitor_connector="DP-1",
        hide_mode="shortcut-only",
        dock_shortcut="Control+F2",
        applet_prefs={"clock": {"show_digital": False}},
        item_prefs={"browser.desktop": {"custom_icon": "icon.png"}},
    )
    path = tmp_path / "shared.json"
    imported_config.save(path)
    previous = path.read_bytes()
    monkeypatch.setattr(dock_import, "list_theme_names", lambda: ["default"])
    base = Config(pinned=[PinnedEntry("applet", "applet://clock")])

    (source,) = dock_import.read_dock_import_sources(file_path=path)
    result = _prepare(source, base, first_run=False)

    assert result.added == ["browser.desktop", picture.as_uri()]
    assert result.config.pinned == [*base.pinned, *result.added]
    assert result.changes["transparency"] == 0.7
    assert result.changes["anchor_applets"] is True
    assert result.changes["monitor_connector"] == "DP-1"
    assert result.config.theme == base.theme
    assert result.config.hide_mode == base.hide_mode
    assert result.config.dock_shortcut == base.dock_shortcut
    assert result.config.applet_prefs == base.applet_prefs
    assert result.config.item_prefs == base.item_prefs
    assert any("missing.desktop" in note for note in result.notes)
    assert "The source theme is unavailable." in result.notes
    assert "Set up the dock reveal shortcut in Docking Preferences." in result.notes
    assert path.read_bytes() == previous


def test_docking_file_accepts_legacy_pins_and_preference_names(tmp_path):
    path = tmp_path / "shared.json"
    path.write_text(
        '{"pinned": ["editor.desktop"], "folder_stack_unfold": "hover",'
        ' "previews_enabled": false, "zoom_range": {"invalid": true}}',
        encoding="utf-8",
    )
    (source,) = dock_import.read_dock_import_sources(file_path=path)
    result = _prepare(
        source,
        Config(stack_unfold="click", window_preview_thumbnails_enabled=True),
        first_run=False,
    )
    assert result.added == ["editor.desktop"]
    assert result.changes["stack_unfold"] == "hover"
    assert result.changes["window_preview_thumbnails_enabled"] is False
    assert "zoom_range" not in result.config.to_dict()


def test_plank_selects_primary_and_preserves_pins_and_placement(tmp_path, monkeypatch):
    settings = {
        ("/", "enabled-docks"): GLib.Variant("as", ["pinned", "main"]),
        ("docks/pinned", "pinned-only"): GLib.Variant("b", True),
        ("docks/main", "dock-items"): GLib.Variant(
            "as",
            [
                "viewer.dockitem",
                "trash.dockitem",
                "missing.dockitem",
                "../escape.dockitem",
            ],
        ),
        ("docks/main", "icon-size"): GLib.Variant("i", 24),
        ("docks/main", "offset"): GLib.Variant("i", -40),
        ("docks/main", "hide-mode"): GLib.Variant("s", "auto"),
        ("docks/main", "zoom-percent"): GLib.Variant("i", 175),
        ("docks/main", "monitor"): GLib.Variant("s", "DP-1"),
        ("docks/main", "anchor-files"): GLib.Variant("b", True),
    }
    monkeypatch.setattr(
        dock_import,
        "_read_plank_settings",
        lambda: lambda group, key, _signature, _effective: settings.get((group, key)),
    )
    _write(
        tmp_path,
        "main/launchers/a.dockitem",
        "[PlankDockItemPreferences]\nLauncher=browser.desktop\n",
    )
    _write(
        tmp_path,
        "main/launchers/trash.dockitem",
        "[PlankItemsDockItemPreferences]\nLauncher=docklet://trash\n",
    )
    _write(
        tmp_path,
        "main/launchers/viewer.dockitem",
        "[PlankDockItemPreferences]\nLauncher=file:///usr/share/applications/vendor/viewer.desktop\n",
    )
    (tmp_path / "main/launchers/socket.dockitem").symlink_to("/dev/null")

    source = dock_import._read_plank(tmp_path)
    result = _prepare(source)
    assert source.profile == "main"
    assert result.config.pinned == [
        "vendor-viewer.desktop",
        "applet://trash",
        "browser.desktop",
    ]
    assert result.config.icon_size == 32
    assert result.config.dock_alignment == 0.3
    assert result.config.hide_mode == "autohide"
    assert result.config.zoom_percent == 1.75
    assert result.config.monitor_connector == "DP-1"
    assert result.config.anchor_files
    assert len(result.notes) == 3
    assert not _prepare(source, first_run=False).config.anchor_files


def test_plank_dconf_fallback_and_empty_dock(tmp_path, monkeypatch):
    monkeypatch.setattr(
        dock_import,
        "Gio",
        SimpleNamespace(
            SettingsSchemaSource=SimpleNamespace(get_default=lambda: None),
        ),
    )
    monkeypatch.setattr(
        dock_import.subprocess,
        "run",
        lambda args, **_kwargs: CompletedProcess(
            args,
            0,
            "[/]\nenabled-docks=['dock1']\n[docks/dock1]\ndock-items=@as []\n"
            "icon-size=72\nposition='left'\nzoom-enabled=not-a-boolean\n",
            "",
        ),
    )
    (tmp_path / "dock1/launchers").mkdir(parents=True)
    result = _prepare(dock_import._read_plank(tmp_path))
    assert result.empty_layout
    assert not result.config.pinned
    assert result.config.icon_size == 72
    assert result.config.position == "left"
    assert "zoom_enabled" not in result.changes


def test_plank_uses_schema_defaults_only_after_profile_detection(tmp_path, monkeypatch):
    defaults = {
        "enabled-docks": GLib.Variant("as", ["dock1"]),
        "hide-mode": GLib.Variant("s", "intelligent"),
        "zoom-enabled": GLib.Variant("b", False),
    }
    schema = SimpleNamespace(has_key=defaults.__contains__)
    schemas = SimpleNamespace(lookup=lambda _schema_id, _recursive: schema)
    gio_settings = SimpleNamespace(
        get_user_value=lambda _key: None,
        get_value=defaults.__getitem__,
    )
    monkeypatch.setattr(
        dock_import,
        "Gio",
        SimpleNamespace(
            SettingsSchemaSource=SimpleNamespace(get_default=lambda: schemas),
            Settings=SimpleNamespace(new_full=lambda *_args: gio_settings),
        ),
    )
    assert not dock_import._read_plank(tmp_path).available
    _write(
        tmp_path,
        "dock1/launchers/browser.dockitem",
        "[PlankDockItemPreferences]\nLauncher=browser.desktop\n",
    )

    result = _prepare(dock_import._read_plank(tmp_path))
    assert result.config.hide_mode == "intelligent"
    assert result.config.zoom_enabled is False
    assert result.config.pinned == ["browser.desktop"]


def test_review_explains_gap_clamping_even_when_current_gap_is_the_same():
    source = dock_import.DockImportSource("Plank", edge_gap=160)
    result = _prepare(source, Config(additional_distance_from_edge=100))

    assert not result.changes
    assert result.notes == ["Adjusted the screen edge gap from 160 px to 104 px."]


def test_plank_detects_a_single_unlisted_profile_with_only_preferences(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        dock_import,
        "_read_plank_settings",
        lambda: (
            lambda group, key, _signature, _effective: (
                GLib.Variant("i", 64)
                if (group, key) == ("docks/work", "icon-size")
                else None
            )
        ),
    )
    (tmp_path / "work/launchers").mkdir(parents=True)

    source = dock_import._read_plank(tmp_path)

    assert source.profile == "work"
    assert _prepare(source).config.icon_size == 64
    assert not source.empty_layout


def test_plank_without_settings_uses_files_and_declines_ambiguous_profiles(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(dock_import, "_read_plank_settings", lambda: None)
    for profile in ("work", "personal"):
        _write(
            tmp_path,
            f"{profile}/launchers/app.dockitem",
            "[PlankDockItemPreferences]\nLauncher=browser.desktop\n",
        )
    source = dock_import._read_plank(tmp_path)
    assert not source.available
    assert source.notes == ["Plank’s primary dock could not be determined."]
    (tmp_path / "work").rename(tmp_path / "dock1")
    source = dock_import._read_plank(tmp_path)
    assert source.profile == "dock1"
    assert _prepare(source).config.pinned == ["browser.desktop"]
    assert source.notes == [
        "Plank settings were unavailable. Launcher files are listed by filename."
    ]


def test_cairo_import_flattens_subdocks_and_interleaves_active_applets(tmp_path):
    _write(
        tmp_path,
        "cairo-dock.conf",
        """[Position]
screen border=2
alignment=.25
screen_name=DP-1
y gap=15
[Accessibility]
visibility=3
lock icons=true
[Icons]
launcher size=38;64
zoom max=1.7
[TaskBar]
show applications=false
current desktop only=true
[System]
modules=Clock;Moon;Calendar;Folders;Unknown
""",
    )
    for name, keys in {
        "99-browser": "Order=1.5\nOrigin=missing.desktop;browser.desktop;\nIcon Type=0",
        "10-open": "Order=4\nName=Office\nIcon Type=1",
        "20-editor": "Order=-1\nContainer=Office\nOrigin=application://editor.desktop",
        "30-nested": "Order=4\nContainer=Office\nName=Nested\nType=Container",
        "50-cycle": "Order=3\nContainer=Nested\nName=Office\nIcon Type=1",
        "51-separator": "Order=5\nContainer=Nested\nIcon Type=2",
        "52-separator": "Order=6\nContainer=Nested\nIcon Type=2",
        "60-custom": "Order=8\nOrigin=browser.desktop\nExec=browser --custom",
        "61-second-dock": "Order=9\nContainer=_MainDock_-2\nOrigin=editor.desktop",
        "62-missing": "Order=10\nOrigin=missing.desktop",
    }.items():
        _write(tmp_path, f"launchers/{name}.desktop", f"[Desktop Entry]\n{keys}\n")
    _write(tmp_path, "plug-ins/clock/clock.conf", "[Icon]\norder=3.5\n")
    _write(tmp_path, "plug-ins/Moon/Moon.conf", "[Icon]\norder=1\ndock name=Office\n")
    _write(
        tmp_path,
        "plug-ins/Calendar/Calendar.conf",
        "[Icon]\norder=0\n[Desklet]\ninitially detached=true\n",
    )
    _write(tmp_path, "plug-ins/inactive/inactive.conf", "[Icon]\norder=3\n")
    folder = tmp_path / "Ordinary folder"
    folder.mkdir()
    _write(
        tmp_path,
        "plug-ins/Folders/Folders.conf",
        f"[Icon]\norder=7\n[Configuration]\ndir path={folder.as_uri()}\n",
    )

    source = dock_import._read_cairo(tmp_path)
    result = _prepare(source)
    assert result.config.pinned == [
        "browser.desktop",
        "applet://clock",
        "editor.desktop",
        "applet://moon",
        "applet://separator#0",
        "applet://separator#1",
        folder.as_uri(),
    ]
    assert result.config.position == "right"
    assert result.config.dock_alignment == 0.25
    assert result.config.monitor_connector == "DP-1"
    assert result.config.additional_distance_from_edge == 11
    assert result.config.icon_size == 64
    assert result.config.zoom_percent == 1.7
    assert result.config.hide_mode == "dodge-active"
    assert result.config.lock_icons
    assert result.config.pinned_only
    assert result.config.current_workspace_only
    assert set(result.notes) == {
        "Skipped repeated sub-dock Office.",
        "Skipped separate dock _MainDock_-2.",
        "Skipped custom launcher 60-custom.",
        "Skipped detached desklet Calendar.",
        "Skipped plug-in Unknown.",
        "Application unavailable for 62-missing.",
    }
    existing = Config(
        pinned=[
            PinnedEntry("applet", "applet://separator#4"),
            PinnedEntry("app", "browser.desktop"),
        ],
        anchor_applets=True,
    )
    manual = _prepare(source, existing, first_run=False)
    assert manual.config.pinned[:2] == existing.pinned
    assert manual.config.anchor_applets
    assert manual.config.pinned.count("applet://separator#4") == 1
    assert manual.config.pinned.count("applet://separator#0") == 1
    repeated = _prepare(source, manual.config, first_run=False)
    assert not repeated.changes
    assert not repeated.added


def test_read_sources_detects_both_families_and_distinguishes_empty_from_malformed(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(dock_import, "_read_plank_settings", lambda: None)
    _write(
        tmp_path,
        "plank/dock1/launchers/app.dockitem",
        "[PlankDockItemPreferences]\nLauncher=browser.desktop\n",
    )
    _write(
        tmp_path,
        "cairo-dock/current_theme/cairo-dock.conf",
        "[Position]\nalignment=0.7\n",
    )
    launchers = tmp_path / "cairo-dock/current_theme/launchers"
    launchers.mkdir()
    plank, cairo = dock_import.read_dock_import_sources()
    assert plank.pins and cairo.empty_layout
    assert dock_import.read_dock_import_sources("plank") == [plank]
    assert dock_import.read_dock_import_sources("cairo-dock") == [cairo]
    _write(launchers, "invalid.desktop", "not a key file")
    _write(launchers, "empty.desktop", "[Desktop Entry]\n")
    _, cairo = dock_import.read_dock_import_sources()
    assert not cairo.empty_layout
    assert not cairo.pins
    assert len(cairo.notes) == 2
