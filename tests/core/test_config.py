"""Tests for config loading, saving, and defaults."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import docking.core.config as config_mod
from docking.core.config import (
    APP_KIND,
    APPLET_KIND,
    FILE_KIND,
    FOLDER_KIND,
    Config,
    HideMode,
    PinnedEntry,
)
from docking.core.position import Position


def create_symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:
        if os.name == "nt" and exc.winerror == 1314:
            pytest.skip("Symbolic links require Windows Developer Mode or elevation")
        raise


class TestConfigDefaults:
    def test_defaults(self):
        # Given / When
        c = Config()
        # Then
        assert c.icon_size == 48
        assert c.zoom_enabled is True
        assert c.zoom_percent == 1.5
        assert c.zoom_range == 3
        assert c.position == "bottom"
        assert c.dock_alignment == 0.5
        assert c.monitor_index == -1
        assert c.hide_mode == "none"
        assert c.hide_delay_ms == 0
        assert c.window_preview_thumbnails_enabled is True
        assert c.tooltips_enabled is True
        assert c.update_check_enabled is True
        assert c.update_check_interval_hours == 24
        assert c.startup_tips_enabled is True
        assert c.left_click_action == "toggle"
        assert c.middle_click_action == "new-window"
        assert c.dock_item_click_effects_enabled is True
        assert c.dock_item_click_effect == "bounce"
        assert c.stack_unfold == "hover"
        assert c.window_list_sort == "default"
        assert c.show_window_count_numbers is False
        assert c.show_launcher_badges is True
        assert c.show_launcher_progress is True
        assert c.global_search_enabled is True
        assert c.global_search_shortcut == "CTRL+ALT+space"
        assert c.global_search_web_engine == "duckduckgo"
        assert c.theme == "default"
        assert c.transparency == 1.0
        assert isinstance(c.pinned, list)

    def test_window_preview_thumbnails_enabled_default_true(self):
        # Given / When
        c = Config()
        # Then
        assert c.window_preview_thumbnails_enabled is True

    def test_window_preview_highlight_thumbnails_on_hover_default_false(self):
        # Given / When
        c = Config()
        # Then
        assert c.window_preview_highlight_thumbnails_on_hover is False

    def test_window_preview_highlight_thumbnails_on_hover_normalizes_and_roundtrips(
        self,
    ):
        # Given / When
        on = Config(window_preview_highlight_thumbnails_on_hover="yes")
        junk = Config(window_preview_highlight_thumbnails_on_hover="garbage")
        # Then
        assert on.window_preview_highlight_thumbnails_on_hover is True
        assert junk.window_preview_highlight_thumbnails_on_hover is False
        assert on.to_dict()["window_preview_highlight_thumbnails_on_hover"] is True

    def test_window_preview_thumbnail_width_default_200(self):
        # Given / When
        c = Config()
        # Then
        assert c.window_preview_thumbnail_width == 200
        assert c.to_dict()["window_preview_thumbnail_width"] == 200

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (320, 320),
            ("320", 320),
            (10, 120),
            (9999, 400),
            ("garbage", 200),
            (None, 200),
        ],
    )
    def test_window_preview_thumbnail_width_normalizes(self, raw, expected):
        # Given / When
        c = Config(window_preview_thumbnail_width=raw)
        # Then
        assert c.window_preview_thumbnail_width == expected

    def test_window_preview_outline_window_on_hover_default_false(self):
        # Given / When
        c = Config()
        # Then
        assert c.window_preview_outline_window_on_hover is False

    def test_window_preview_outline_window_on_hover_normalizes_and_roundtrips(self):
        # Given / When
        on = Config(window_preview_outline_window_on_hover="yes")
        off = Config(window_preview_outline_window_on_hover="garbage")
        # Then
        assert on.window_preview_outline_window_on_hover is True
        assert off.window_preview_outline_window_on_hover is False
        assert on.to_dict()["window_preview_outline_window_on_hover"] is True

    def test_hide_delay_default_zero(self):
        # Given / When
        c = Config()
        # Then
        assert c.hide_delay_ms == 0

    def test_pos_property_returns_position_enum(self):
        c = Config(position="left")
        assert c.pos is Position.LEFT

    def test_scaled_icon_and_hide_mode_properties(self):
        c = Config(icon_size=40, zoom_percent=1.25, hide_mode="intelligent")

        assert c.scaled_icon_size == 50
        assert c.hide_mode_enum is HideMode.INTELLIGENT

    def test_post_init_normalizes_invalid_runtime_values(self):
        c = Config(
            icon_size="bad",
            zoom_percent="bad",
            zoom_range="bad",
            position=object(),
            monitor_index="bad",
            hide_delay_ms="bad",
            transparency="bad",
            window_preview_thumbnails_enabled="off",
            lock_icons="on",
            current_workspace_only=0,
            anchor_applets=1,
            active_display="no",
            update_check_enabled="off",
            update_check_interval_hours="bad",
            startup_tips_enabled="off",
            show_window_count_numbers="on",
            show_launcher_badges="off",
            show_launcher_progress=0,
            theme="",
        )

        assert c.icon_size == 48
        assert c.zoom_percent == 1.5
        assert c.zoom_range == 3
        assert c.position == "bottom"
        assert c.monitor_index == -1
        assert c.hide_delay_ms == 0
        assert c.transparency == 1.0
        assert c.window_preview_thumbnails_enabled is False
        assert c.lock_icons is True
        assert c.current_workspace_only is False
        assert c.anchor_applets is True
        assert c.active_display is False
        assert c.update_check_enabled is False
        assert c.update_check_interval_hours == 24
        assert c.startup_tips_enabled is False
        assert c.show_window_count_numbers is True
        assert c.show_launcher_badges is False
        assert c.show_launcher_progress is False
        assert c.theme == "default"

    def test_global_search_values_are_normalized(self):
        malformed: Any = {
            "global_search_enabled": "off",
            "global_search_shortcut": " ",
            "global_search_web_engine": "unknown",
        }
        c = Config(**malformed)

        assert c.global_search_enabled is False
        assert c.global_search_shortcut == "CTRL+ALT+space"
        assert c.global_search_web_engine == "duckduckgo"

    def test_dock_item_click_effect_values_are_normalized(self):
        disabled = Config(
            dock_item_click_effects_enabled="off", dock_item_click_effect="bounce"
        )
        invalid = Config(dock_item_click_effect="unknown")

        assert disabled.dock_item_click_effects_enabled is False
        assert disabled.dock_item_click_effect == "bounce"
        assert invalid.dock_item_click_effect == "bounce"


class TestConfigLoad:
    @pytest.mark.parametrize(
        ("alignment", "expected_alignment"),
        [
            ("0.25", 0.25),
            (-1, 0.0),
            (2, 1.0),
            ("bad", 0.5),
            (None, 0.5),
            (float("nan"), 0.5),
            (float("inf"), 0.5),
        ],
    )
    def test_alignment_normalizes_and_roundtrips(
        self, tmp_path, alignment, expected_alignment
    ):
        path = tmp_path / "dock.json"
        path.write_text(json.dumps({"dock_alignment": alignment}))
        config = Config.load(path)
        assert config.dock_alignment == expected_alignment
        config.save()
        loaded = Config.load(path)
        assert loaded.dock_alignment == expected_alignment

    def test_older_config_keeps_centered_alignment(self, tmp_path):
        path = tmp_path / "dock.json"
        path.write_text('{"position": "left"}')
        config = Config.load(path)
        assert config.dock_alignment == 0.5

    def test_load_missing_file_creates_default(self, tmp_path, monkeypatch):
        # Given
        path = tmp_path / "dock.json"
        seeded = [
            PinnedEntry(kind=APP_KIND, target="browser.desktop"),
            PinnedEntry(kind=APPLET_KIND, target="applet://clock"),
        ]
        initial_pinned_factory = MagicMock(return_value=seeded)
        original_save = Config.save
        saves: list[Path] = []

        def recording_save(config, path=None):
            saves.append(Path(path))
            return original_save(config, path=path)

        monkeypatch.setattr(Config, "save", recording_save)
        # When
        config = Config.load(
            path,
            initial_pinned_factory=initial_pinned_factory,
        )
        # Then
        assert config.icon_size == 48
        assert config.pinned == seeded
        assert path.exists()
        initial_pinned_factory.assert_called_once_with()
        assert saves == [path]

    def test_load_missing_file_seeds_starter_applets_after_launchers(self, tmp_path):
        path = tmp_path / "dock.json"
        available = {
            "firefox.desktop",
            "files.desktop",
            "gnome-terminal.desktop",
            "editor.desktop",
            "mail.desktop",
            "gnome-calculator.desktop",
            "gnome-software.desktop",
        }
        defaults = {
            "inode/directory": "files.desktop",
            "text/plain": "editor.desktop",
            "x-scheme-handler/mailto": "mail.desktop",
        }

        config = Config.load(
            path,
            initial_pinned_factory=lambda: config_mod.build_initial_pinned(
                desktop_id_exists=available.__contains__,
                default_desktop_id_for=defaults.get,
            ),
        )

        assert config.pinned == [
            PinnedEntry(kind=APPLET_KIND, target="applet://applications"),
            PinnedEntry(kind=APP_KIND, target="firefox.desktop"),
            PinnedEntry(kind=APP_KIND, target="files.desktop"),
            PinnedEntry(kind=APP_KIND, target="gnome-terminal.desktop"),
            PinnedEntry(kind=APP_KIND, target="editor.desktop"),
            PinnedEntry(kind=APP_KIND, target="mail.desktop"),
            PinnedEntry(kind=APP_KIND, target="gnome-calculator.desktop"),
            PinnedEntry(kind=APP_KIND, target="gnome-software.desktop"),
            PinnedEntry(kind=APPLET_KIND, target="applet://clock"),
            PinnedEntry(kind=APPLET_KIND, target="applet://calendar"),
            PinnedEntry(kind=APPLET_KIND, target="applet://weather"),
            PinnedEntry(kind=APPLET_KIND, target="applet://systemmonitor"),
            PinnedEntry(kind=APPLET_KIND, target="applet://hydration"),
            PinnedEntry(kind=APPLET_KIND, target="applet://notifications"),
            PinnedEntry(kind=APPLET_KIND, target="applet://session"),
        ]

    def test_load_missing_file_without_factory_uses_pure_applet_fallback(
        self, tmp_path
    ):
        path = tmp_path / "dock.json"

        config = Config.load(path)

        assert config.pinned == [
            PinnedEntry(kind=APPLET_KIND, target=f"applet://{applet_id}")
            for applet_id in config_mod.STARTER_APPLET_IDS
        ]

    def test_load_window_preview_thumbnails_enabled(self, tmp_path):
        # Given
        path = tmp_path / "dock.json"
        data = {"window_preview_thumbnails_enabled": False}
        path.write_text(json.dumps(data))
        # When
        config = Config.load(path)
        # Then
        assert config.window_preview_thumbnails_enabled is False

    @pytest.mark.parametrize(
        ("legacy_key", "new_key"),
        [
            ("previews_enabled", "window_preview_thumbnails_enabled"),
            (
                "preview_thumbnail_outline",
                "window_preview_highlight_thumbnails_on_hover",
            ),
        ],
    )
    @pytest.mark.parametrize("value", [True, False, "on", "off"])
    def test_legacy_window_preview_settings_migrate_and_save(
        self, tmp_path, legacy_key, new_key, value
    ):
        path = tmp_path / "dock.json"
        path.write_text(json.dumps({legacy_key: value}))

        config = Config.load(path)

        assert getattr(config, new_key) is (value in (True, "on"))
        config.save()
        saved = json.loads(path.read_text())
        assert saved[new_key] is getattr(config, new_key)
        assert legacy_key not in saved
        assert getattr(Config.load(path), new_key) is getattr(config, new_key)

    @pytest.mark.parametrize(
        ("legacy_key", "new_key"),
        [
            ("previews_enabled", "window_preview_thumbnails_enabled"),
            (
                "preview_thumbnail_outline",
                "window_preview_highlight_thumbnails_on_hover",
            ),
        ],
    )
    @pytest.mark.parametrize("value", [True, False])
    def test_new_window_preview_settings_take_precedence(
        self, tmp_path, legacy_key, new_key, value
    ):
        path = tmp_path / "dock.json"
        path.write_text(json.dumps({legacy_key: not value, new_key: value}))

        assert getattr(Config.load(path), new_key) is value

    def test_load_tooltips_enabled(self, tmp_path):
        # Given
        path = tmp_path / "dock.json"
        data = {"tooltips_enabled": False}
        path.write_text(json.dumps(data))
        # When
        config = Config.load(path)
        # Then
        assert config.tooltips_enabled is False

    def test_load_valid_file(self, tmp_path):
        # Given
        path = tmp_path / "dock.json"
        data = {"icon_size": 64, "zoom_percent": 1.5, "pinned": ["foo.desktop"]}
        path.write_text(json.dumps(data))
        # When
        config = Config.load(path)
        # Then
        assert config.icon_size == 64
        assert config.zoom_percent == 1.5
        assert config.pinned == [PinnedEntry(kind=APP_KIND, target="foo.desktop")]
        # Unspecified keys use defaults
        assert config.hide_mode == "none"

    def test_load_ignores_unknown_keys(self, tmp_path):
        # Given
        path = tmp_path / "dock.json"
        data = {"icon_size": 32, "unknown_key": "value"}
        path.write_text(json.dumps(data))
        # When
        config = Config.load(path)
        # Then
        assert config.icon_size == 32
        assert not hasattr(config, "unknown_key")

    @pytest.mark.parametrize(
        ("field", "raw", "expected"),
        [
            ("icon_size", float("inf"), 48),
            ("icon_size", float("-inf"), 48),
            ("icon_size", float("nan"), 48),
            ("zoom_percent", "nan", 1.5),
            ("zoom_percent", float("inf"), 1.5),
            ("zoom_percent", float("-inf"), 1.5),
            ("zoom_percent", 10**400, 1.5),
            ("transparency", "NaN", 1.0),
        ],
    )
    def test_load_invalid_numbers_preserves_other_settings(
        self, tmp_path, field, raw, expected
    ):
        path = tmp_path / "dock.json"
        path.write_text(
            json.dumps({field: raw, "theme": "glass", "pinned": ["foo.desktop"]})
        )

        config = Config.load(path)

        assert config.theme == "glass"
        assert config.pinned == [PinnedEntry(kind=APP_KIND, target="foo.desktop")]
        assert getattr(config, field) == expected
        assert config.scaled_icon_size == 72
        config.save()
        json.loads(path.read_text(), parse_constant=lambda value: pytest.fail(value))

    @pytest.mark.parametrize("last_closed", [float("nan"), float("inf"), float("-inf")])
    def test_load_drops_invalid_recent_timestamps_preserving_settings(
        self, tmp_path, last_closed
    ):
        path = tmp_path / "dock.json"
        valid_entry = {"desktop_id": "good.desktop", "last_closed": 1234}
        path.write_text(
            json.dumps(
                {
                    "theme": "glass",
                    "recent_apps": [
                        {"desktop_id": "bad.desktop", "last_closed": last_closed},
                        valid_entry,
                    ],
                }
            )
        )

        config = Config.load(path)

        assert config.theme == "glass"
        assert config.recent_apps == [valid_entry]

    def test_load_empty_json_uses_defaults(self, tmp_path):
        # Given
        path = tmp_path / "dock.json"
        path.write_text("{}")
        # When
        config = Config.load(path)
        # Then
        assert config.icon_size == 48

    def test_load_invalid_position_falls_back_to_bottom(self, tmp_path):
        path = tmp_path / "dock.json"
        path.write_text(json.dumps({"position": "diagonal"}))

        config = Config.load(path)

        assert config.position == "bottom"

    def test_load_monitor_index_below_minus_one_is_clamped(self, tmp_path):
        path = tmp_path / "dock.json"
        path.write_text(json.dumps({"monitor_index": -5}))

        config = Config.load(path)

        assert config.monitor_index == -1

    def test_load_clamps_scalar_ranges(self, tmp_path):
        path = tmp_path / "dock.json"
        path.write_text(
            json.dumps(
                {
                    "icon_size": 999,
                    "zoom_percent": 0.1,
                    "zoom_range": -4,
                    "hide_delay_ms": -50,
                    "unhide_delay_ms": "-10",
                    "hide_time_ms": -1,
                    "transparency": 9,
                }
            )
        )

        config = Config.load(path)

        assert config.icon_size == 128
        assert config.zoom_percent == 1.0
        assert config.zoom_range == 0
        assert config.hide_delay_ms == 0
        assert config.unhide_delay_ms == 0
        assert config.hide_time_ms == 0
        assert config.transparency == 1.0

    def test_load_normalizes_bool_like_values(self, tmp_path):
        path = tmp_path / "dock.json"
        path.write_text(
            json.dumps(
                {
                    "hide_mode": "autohide",
                    "window_preview_thumbnails_enabled": "false",
                    "tooltips_enabled": "yes",
                    "zoom_enabled": 0,
                    "left_click_action": "cycle",
                    "middle_click_action": "minimize",
                    "folder_stack_unfold": "hover",
                    "window_list_sort": "alphabetical",
                    "show_window_count_numbers": "true",
                    "show_launcher_badges": "false",
                    "show_launcher_progress": "no",
                }
            )
        )

        config = Config.load(path)

        assert config.hide_mode == "autohide"
        assert config.window_preview_thumbnails_enabled is False
        assert config.tooltips_enabled is True
        assert config.zoom_enabled is False
        assert config.left_click_action == "cycle"
        assert config.middle_click_action == "minimize"
        assert config.stack_unfold == "hover"
        assert config.window_list_sort == "alphabetical"
        assert config.show_window_count_numbers is True
        assert config.show_launcher_badges is False
        assert config.show_launcher_progress is False

    def test_load_clamps_transparency_to_minimum(self, tmp_path):
        path = tmp_path / "dock.json"
        path.write_text(json.dumps({"transparency": 0.01}))

        config = Config.load(path)

        assert config.transparency == 0.15

    def test_load_invalid_click_actions_fall_back_to_defaults(self, tmp_path):
        path = tmp_path / "dock.json"
        path.write_text(
            json.dumps(
                {
                    "left_click_action": "explode",
                    "middle_click_action": "quit-all",
                    "folder_stack_unfold": "peek",
                    "window_list_sort": "reverse",
                }
            )
        )

        config = Config.load(path)

        assert config.left_click_action == "toggle"
        assert config.middle_click_action == "new-window"
        assert config.stack_unfold == "hover"
        assert config.window_list_sort == "default"

    def test_load_ignores_legacy_autohide_key(self, tmp_path):
        path = tmp_path / "dock.json"
        path.write_text(json.dumps({"autohide": True}))

        config = Config.load(path)

        assert config.hide_mode == "none"

    def test_load_migrates_legacy_string_pins_to_typed_entries(self, tmp_path):
        path = tmp_path / "dock.json"
        file_uri = (tmp_path / "notes.txt").as_uri()
        folder_uri = tmp_path.as_uri()
        path.write_text(
            json.dumps({"pinned": ["firefox.desktop", file_uri, folder_uri]})
        )

        config = Config.load(path)

        assert config.pinned == [
            PinnedEntry(kind=APP_KIND, target="firefox.desktop"),
            PinnedEntry(kind=FILE_KIND, target=file_uri),
            PinnedEntry(kind=FOLDER_KIND, target=folder_uri),
        ]

    def test_load_drops_malformed_pins(self, tmp_path):
        path = tmp_path / "dock.json"
        path.write_text(
            json.dumps(
                {
                    "pinned": [
                        {"kind": "app", "target": "firefox.desktop"},
                        {"kind": "broken"},
                        {"kind": "nope", "target": "bad"},
                        "",
                    ]
                }
            )
        )

        config = Config.load(path)

        assert config.pinned == [
            PinnedEntry(kind=APP_KIND, target="firefox.desktop"),
        ]

    def test_load_drops_duplicate_pins_keeping_first_occurrence(self, tmp_path):
        """A repeated pin must not become a second copy of the same dock item.

        The model writes its list straight back to disk, so a duplicate that
        survived loading would be drawn twice and persist across every restart.
        """
        path = tmp_path / "dock.json"
        path.write_text(
            json.dumps(
                {
                    "pinned": [
                        {"kind": "app", "target": "first.desktop"},
                        {"kind": "app", "target": "second.desktop"},
                        {"kind": "app", "target": "first.desktop"},
                        "first.desktop",
                        {"kind": "applet", "target": "applet://clock"},
                        {"kind": "applet", "target": "applet://clock"},
                    ]
                }
            )
        )

        config = Config.load(path)

        # The legacy string form spells the same entry, and the first one wins.
        assert config.pinned == [
            PinnedEntry(kind=APP_KIND, target="first.desktop"),
            PinnedEntry(kind=APP_KIND, target="second.desktop"),
            PinnedEntry(kind=APPLET_KIND, target="applet://clock"),
        ]

    def test_load_keeps_same_target_under_different_kinds(self, tmp_path):
        """Only identical kind+target pairs are duplicates."""
        path = tmp_path / "dock.json"
        file_uri = (tmp_path / "notes.txt").as_uri()
        path.write_text(
            json.dumps(
                {
                    "pinned": [
                        {"kind": "file", "target": file_uri},
                        {"kind": "folder", "target": file_uri},
                    ]
                }
            )
        )

        config = Config.load(path)

        assert config.pinned == [
            PinnedEntry(kind=FILE_KIND, target=file_uri),
            PinnedEntry(kind=FOLDER_KIND, target=file_uri),
        ]

    @pytest.mark.parametrize("bad_pinned", [None, 5, 1.5, True, "firefox.desktop"])
    def test_load_survives_a_pinned_value_that_is_not_a_list(
        self, tmp_path, bad_pinned
    ):
        """One malformed field must not reset every other setting.

        Iterating the value whatever its JSON type aborted the whole load, and
        a bare string became one pin per character.
        """
        path = tmp_path / "dock.json"
        path.write_text(
            json.dumps(
                {
                    "icon_size": 96,
                    "theme": "glass",
                    "position": "top",
                    "anchor_applets": True,
                    "pinned": bad_pinned,
                }
            )
        )

        config = Config.load(path)

        assert config.icon_size == 96
        assert config.theme == "glass"
        assert config.position == "top"
        assert config.anchor_applets is True
        assert config.pinned == []

    def test_load_filters_invalid_pref_map_shapes(self, tmp_path):
        path = tmp_path / "dock.json"
        path.write_text(
            json.dumps(
                {
                    "applet_prefs": {
                        "clock": {"show_seconds": True},
                        "broken": [],
                    },
                    "item_prefs": ["wrong"],
                }
            )
        )

        config = Config.load(path)

        assert config.applet_prefs == {"clock": {"show_seconds": True}}
        assert config.item_prefs == {}

    def test_load_falls_back_to_backup_when_primary_is_invalid(self, tmp_path):
        path = tmp_path / "dock.json"
        backup = tmp_path / "dock.json.bak"
        path.write_text("{", encoding="utf-8")
        backup.write_text(json.dumps({"icon_size": 72}), encoding="utf-8")

        config = Config.load(path)

        assert config.icon_size == 72
        assert config._path == path

    @pytest.mark.parametrize(
        ("target_backup", "expected_size"),
        [(None, 96), ("{", 96), ('{"icon_size": 72}', 72)],
    )
    def test_symlink_recovers_legacy_backup_after_upgrade(
        self, tmp_path, target_backup, expected_size
    ):
        target = tmp_path / "dotfiles" / "dock.json"
        target.parent.mkdir()
        target.write_text("{", encoding="utf-8")
        link = tmp_path / "dock.json"
        create_symlink(link, target)
        legacy_backup = link.with_name("dock.json.bak")
        legacy_backup.write_text(
            json.dumps(
                {"icon_size": 96, "theme": "glass", "pinned": ["saved.desktop"]}
            ),
            encoding="utf-8",
        )
        if target_backup is not None:
            target.with_name("dock.json.bak").write_text(
                target_backup, encoding="utf-8"
            )

        config = Config.load(link)

        assert config.icon_size == expected_size
        assert config._path == target
        if expected_size == 96:
            assert config.theme == "glass"
            assert config.pinned == [PinnedEntry(kind=APP_KIND, target="saved.desktop")]
        config.save()
        assert link.is_symlink()
        assert json.loads(target.read_text())["icon_size"] == expected_size
        assert json.loads(legacy_backup.read_text())["icon_size"] == 96

    def test_load_creates_backup_when_primary_is_valid_and_backup_missing(
        self, tmp_path
    ):
        path = tmp_path / "dock.json"
        backup = tmp_path / "dock.json.bak"
        path.write_text(json.dumps({"icon_size": 72}), encoding="utf-8")

        config = Config.load(path)

        assert config.icon_size == 72
        assert backup.exists()
        assert json.loads(backup.read_text())["icon_size"] == 72

    def test_load_existing_file_does_not_reseed_first_run_pins(self, tmp_path):
        path = tmp_path / "dock.json"
        path.write_text(json.dumps({"pinned": ["existing.desktop"]}), encoding="utf-8")

        def fail_if_called():
            raise AssertionError("first-run seeding should not run for existing config")

        config = Config.load(path, initial_pinned_factory=fail_if_called)

        assert config.pinned == [PinnedEntry(kind=APP_KIND, target="existing.desktop")]

    def test_load_existing_empty_pins_are_never_reseeded(self, tmp_path):
        path = tmp_path / "dock.json"
        path.write_text(json.dumps({"pinned": []}), encoding="utf-8")
        initial_pinned_factory = MagicMock(
            side_effect=AssertionError("existing empty pins must remain empty")
        )

        config = Config.load(
            path,
            initial_pinned_factory=initial_pinned_factory,
        )

        assert config.pinned == []
        initial_pinned_factory.assert_not_called()

    def test_load_invalid_primary_and_backup_falls_back_to_default(self, tmp_path):
        path = tmp_path / "dock.json"
        backup = tmp_path / "dock.json.bak"
        path.write_text("{", encoding="utf-8")
        backup.write_text("{", encoding="utf-8")

        config = Config.load(path)

        assert config.icon_size == 48
        assert config._path == path

    def test_load_valid_primary_ignores_backup_creation_failure(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "dock.json"
        path.write_text(json.dumps({"icon_size": 72}), encoding="utf-8")
        monkeypatch.setattr(
            config_mod,
            "_write_backup_copy",
            lambda **_kwargs: (_ for _ in ()).throw(OSError("boom")),
        )

        config = Config.load(path)

        assert config.icon_size == 72


class TestConfigSave:
    def test_save_creates_parent_dirs(self, tmp_path):
        # Given
        path = tmp_path / "sub" / "dir" / "dock.json"
        config = Config(icon_size=64)
        # When
        config.save(path)
        # Then
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["icon_size"] == 64

    def test_save_roundtrip(self, tmp_path):
        # Given
        path = tmp_path / "dock.json"
        original = Config(
            icon_size=80,
            zoom_percent=1.5,
            monitor_index=1,
            pinned=["a.desktop", "b.desktop"],
        )
        # When
        original.save(path)
        loaded = Config.load(path)
        # Then
        assert loaded.icon_size == 80
        assert loaded.zoom_percent == 1.5
        assert loaded.monitor_index == 1
        assert loaded.pinned == [
            PinnedEntry(kind=APP_KIND, target="a.desktop"),
            PinnedEntry(kind=APP_KIND, target="b.desktop"),
        ]

    def test_save_without_path_uses_loaded_path(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config.load(path)
        config.icon_size = 72

        config.save()

        saved = json.loads(path.read_text())
        assert saved["icon_size"] == 72

    def test_save_writes_through_a_symlinked_config(self, tmp_path):
        """Dotfiles setups symlink the config into a repository.

        The atomic replace unlinked the link, so the repository stopped
        tracking the config after the first save.
        """
        target = tmp_path / "dotfiles" / "dock.json"
        target.parent.mkdir()
        target.write_text(json.dumps({"icon_size": 96}))
        link = tmp_path / "dock.json"
        create_symlink(link, target)
        config = Config.load(link)
        config.icon_size = 32

        config.save()

        assert link.is_symlink()
        assert json.loads(target.read_text())["icon_size"] == 32

    def test_backup_of_a_symlinked_config_is_found_again_through_the_link(
        self, tmp_path
    ):
        """Loading has to look for the backup where saving puts it.

        Resolving the link on save but not on load made recovery restore a
        stale copy.
        """
        target = tmp_path / "dotfiles" / "dock.json"
        target.parent.mkdir()
        target.write_text(json.dumps({"icon_size": 96}))
        link = tmp_path / "dock.json"
        create_symlink(link, target)

        first = Config.load(link)
        first.icon_size = 32
        first.save()
        second = Config.load(link)
        second.icon_size = 64
        second.save()

        target.write_text("{ not json")

        recovered = Config.load(link)

        # The newest good state, not the value from before the first save.
        assert recovered.icon_size == 32

    def test_save_persists_click_actions(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config(left_click_action="cycle", middle_click_action="close-focused")

        config.save(path)

        saved = json.loads(path.read_text())
        assert saved["left_click_action"] == "cycle"
        assert saved["middle_click_action"] == "close-focused"

    def test_save_persists_stack_unfold(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config(stack_unfold="hover")

        config.save(path)

        saved = json.loads(path.read_text())
        assert saved["stack_unfold"] == "hover"
        assert "folder_stack_unfold" not in saved

    def test_new_stack_unfold_takes_precedence_over_legacy_key(self, tmp_path):
        path = tmp_path / "dock.json"
        path.write_text(
            json.dumps({"stack_unfold": "click", "folder_stack_unfold": "hover"})
        )

        config = Config.load(path)

        assert config.stack_unfold == "click"

    def test_save_persists_window_list_sort(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config(window_list_sort="alphabetical")

        config.save(path)

        saved = json.loads(path.read_text())
        assert saved["window_list_sort"] == "alphabetical"

    @pytest.mark.parametrize("effect", ["bounce", "pulse", "wobble"])
    def test_dock_item_click_effect_settings_roundtrip(self, tmp_path, effect):
        path = tmp_path / "dock.json"
        Config(
            dock_item_click_effects_enabled=False, dock_item_click_effect=effect
        ).save(path)

        saved = json.loads(path.read_text())
        loaded = Config.load(path)
        assert saved["dock_item_click_effects_enabled"] is False
        assert saved["dock_item_click_effect"] == effect
        assert loaded.dock_item_click_effects_enabled is False
        assert loaded.dock_item_click_effect == effect

    def test_save_persists_show_window_count_numbers(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config(show_window_count_numbers=True)

        config.save(path)

        saved = json.loads(path.read_text())
        assert saved["show_window_count_numbers"] is True

    def test_save_persists_launcher_overlay_preferences(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config(
            show_launcher_badges=False,
            show_launcher_progress=False,
        )

        config.save(path)

        saved = json.loads(path.read_text())
        assert saved["show_launcher_badges"] is False
        assert saved["show_launcher_progress"] is False

    def test_save_persists_transparency(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config(transparency=0.65)

        config.save(path)

        saved = json.loads(path.read_text())
        assert saved["transparency"] == 0.65

    def test_save_persists_update_check_preferences(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config(
            update_check_enabled=False,
            update_check_interval_hours=168,
            startup_tips_enabled=False,
        )

        config.save(path)

        saved = json.loads(path.read_text())
        assert saved["update_check_enabled"] is False
        assert saved["update_check_interval_hours"] == 168
        assert saved["startup_tips_enabled"] is False

    def test_save_persists_typed_pinned_entries(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config(
            pinned=[
                PinnedEntry(kind=APP_KIND, target="firefox.desktop"),
                PinnedEntry(kind=FILE_KIND, target=(tmp_path / "notes.txt").as_uri()),
            ]
        )

        config.save(path)

        saved = json.loads(path.read_text())
        assert saved["pinned"] == [
            {"kind": "app", "target": "firefox.desktop"},
            {"kind": "file", "target": (tmp_path / "notes.txt").as_uri()},
        ]

    def test_save_writes_canonical_pref_map_shape(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config(
            applet_prefs={"clock": {"show_seconds": True}, "bad": []},
            item_prefs={"item://a": {"show_hidden": False}, "bad": 1},
        )

        config.save(path)

        saved = json.loads(path.read_text())
        assert saved["applet_prefs"] == {"clock": {"show_seconds": True}}
        assert saved["item_prefs"] == {"item://a": {"show_hidden": False}}

    def test_save_replaces_target_atomically(self, monkeypatch, tmp_path):
        path = tmp_path / "dock.json"
        config = Config(icon_size=96)
        replace_calls: list[tuple[Path, Path]] = []

        original_replace = Path.replace

        def recording_replace(src: Path, dst: Path):
            replace_calls.append((src, Path(dst)))
            return original_replace(src, dst)

        monkeypatch.setattr(Path, "replace", recording_replace)

        config.save(path)

        assert len(replace_calls) == 1
        src, dst = replace_calls[0]
        assert src.parent == path.parent
        assert src.name.startswith(f".{path.name}.")
        assert src.suffix == ".tmp"
        assert dst == path
        assert json.loads(path.read_text())["icon_size"] == 96

    def test_save_keeps_last_known_good_backup(self, tmp_path):
        path = tmp_path / "dock.json"
        backup = tmp_path / "dock.json.bak"
        original = Config(icon_size=48)
        original.save(path)

        updated = Config(icon_size=96)
        updated.save(path)

        assert json.loads(path.read_text())["icon_size"] == 96
        assert json.loads(backup.read_text())["icon_size"] == 48

    def test_save_skips_backup_refresh_when_current_file_invalid(self, tmp_path):
        path = tmp_path / "dock.json"
        path.write_text("{", encoding="utf-8")
        config = Config(icon_size=96)

        config.save(path)

        assert json.loads(path.read_text())["icon_size"] == 96
        assert not (tmp_path / "dock.json.bak").exists()

    def test_save_cleans_temp_file_when_write_fails(self, tmp_path, monkeypatch):
        path = tmp_path / "dock.json"
        config = Config(icon_size=96)
        monkeypatch.setattr(
            config_mod,
            "_write_json_atomic_candidate",
            lambda **_kwargs: (_ for _ in ()).throw(OSError("boom")),
        )

        with pytest.raises(OSError):
            config.save(path)

        assert list(tmp_path.iterdir()) == []

    @pytest.mark.skipif(
        os.name == "nt", reason="Directory fsync uses POSIX file descriptors"
    )
    def test_save_fsyncs_directory_after_replace(self, monkeypatch, tmp_path):
        path = tmp_path / "dock.json"
        config = Config(icon_size=96)
        fsynced: list[int] = []

        import docking.core.config as config_mod

        original_fsync = config_mod.os.fsync

        def recording_fsync(fd: int):
            fsynced.append(fd)
            return original_fsync(fd)

        monkeypatch.setattr(config_mod.os, "fsync", recording_fsync)

        config.save(path)

        assert len(fsynced) >= 2

    def test_save_and_backup_with_windows_directory_restrictions(
        self, monkeypatch, tmp_path
    ):
        native_os = config_mod.os

        def windows_open(path, flags, *args, **kwargs):
            if Path(path).is_dir():
                raise PermissionError("Windows CRT cannot open a directory")
            return native_os.open(path, flags, *args, **kwargs)

        monkeypatch.setattr(
            config_mod,
            "os",
            SimpleNamespace(**{**vars(native_os), "name": "nt", "open": windows_open}),
        )
        path = tmp_path / "dock.json"
        config = Config(icon_size=48)
        config.save(path)
        config.icon_size = 64
        config.save(path)

        assert Config.load(path).icon_size == 64
        assert Config.load(path.with_suffix(".json.bak")).icon_size == 48


class TestNormalizeHideMode:
    def test_normalize_hide_mode_returns_default_on_value_error(self, monkeypatch):
        warnings: list[str] = []

        class _Capture:
            def warning(self, msg, *args):
                warnings.append(msg % args)

        monkeypatch.setattr(config_mod, "logger", _Capture())
        result = config_mod._normalize_hide_mode("bogus_mode")
        assert result == "none"
        assert any("Invalid hide mode" in w for w in warnings)

    def test_normalize_hide_mode_non_string_returns_default(self):
        assert config_mod._normalize_hide_mode(42) == "none"
        assert config_mod._normalize_hide_mode(None) == "none"
        assert config_mod._normalize_hide_mode(["autohide"]) == "none"


class TestUriIsDir:
    def test_non_file_uri_returns_false(self):
        assert config_mod._uri_is_dir("http://example.com") is False
        assert config_mod._uri_is_dir("") is False
        assert config_mod._uri_is_dir("not-a-uri") is False

    def test_invalid_uri_returns_false_logs_warning(self, monkeypatch, tmp_path):
        import docking.core.config as config_mod

        warnings: list[str] = []

        class _Capture:
            def warning(self, msg, *args):
                warnings.append(msg % args)

        monkeypatch.setattr(config_mod, "logger", _Capture())

        def _raise_valueerror(_path):
            raise ValueError("unsupported path encoding")

        monkeypatch.setattr(config_mod.Path, "is_dir", _raise_valueerror)
        result = config_mod._uri_is_dir((tmp_path / "bad-path").as_uri())
        assert result is False
        assert any("file URI in persisted config" in w for w in warnings)

    def test_unreadable_path_is_not_fatal_to_the_whole_config(
        self, monkeypatch, tmp_path
    ):
        """A parent directory that cannot be traversed makes is_dir() raise.

        That is an OSError, not a ValueError, so it escaped and aborted the
        whole load; is_dir() is patched because the suite also runs as root.
        """
        warnings: list[str] = []

        class _Capture:
            def warning(self, msg, *args):
                warnings.append(msg % args)

        monkeypatch.setattr(config_mod, "logger", _Capture())

        def _raise_permissionerror(_path):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(config_mod.Path, "is_dir", _raise_permissionerror)
        result = config_mod._uri_is_dir((tmp_path / "locked" / "secret").as_uri())

        assert result is False
        assert any("file URI in persisted config" in w for w in warnings)


class TestNormalizeBool:
    def test_normalize_bool_non_matching_string_returns_default(self):
        assert config_mod._normalize_bool("maybe", default=True) is True
        assert config_mod._normalize_bool("maybe", default=False) is False

    def test_normalize_bool_non_string_type_returns_default(self):
        assert config_mod._normalize_bool([True], default=True) is True
        assert config_mod._normalize_bool(None, default=False) is False


class TestNormalizeInt:
    def test_normalize_int_non_numeric_returns_default(self):
        assert config_mod._normalize_int([1, 2, 3], default=10) == 10
        assert config_mod._normalize_int(None, default=5) == 5

    def test_normalize_int_bool_converts(self):
        assert config_mod._normalize_int(True, default=10) == 1
        assert config_mod._normalize_int(False, default=10) == 0

    def test_normalize_int_clamps_to_range(self):
        assert config_mod._normalize_int(50, default=10, minimum=0, maximum=30) == 30
        assert config_mod._normalize_int(-5, default=10, minimum=0, maximum=30) == 0

    def test_normalize_int_invalid_string_returns_default(self):
        assert config_mod._normalize_int("abc", default=7) == 7


class TestNormalizeFloat:
    def test_normalize_float_bool_converts(self):
        assert config_mod._normalize_float(True, default=1.0) == 1.0
        assert config_mod._normalize_float(False, default=1.0) == 0.0

    def test_normalize_float_non_numeric_returns_default(self):
        assert config_mod._normalize_float([1.0], default=3.5) == 3.5
        assert config_mod._normalize_float(None, default=2.0) == 2.0

    def test_normalize_float_clamps_to_range(self):
        assert (
            config_mod._normalize_float(10.0, default=5.0, minimum=0.0, maximum=3.0)
            == 3.0
        )
        assert (
            config_mod._normalize_float(-1.0, default=5.0, minimum=0.0, maximum=3.0)
            == 0.0
        )

    def test_normalize_float_invalid_string_returns_default(self):
        assert config_mod._normalize_float("xyz", default=2.5) == 2.5


class TestNormalizeOptionalText:
    def test_normalize_optional_text_none_returns_none(self):
        assert config_mod._normalize_optional_text(None) is None

    def test_normalize_optional_text_empty_after_strip_returns_none(self):
        assert config_mod._normalize_optional_text("   ") is None

    def test_normalize_optional_text_returns_stripped(self):
        assert config_mod._normalize_optional_text("  hello  ") == "hello"

    def test_normalize_optional_text_converts_non_string(self):
        assert config_mod._normalize_optional_text(42) == "42"


class TestNormalizeRecentApps:
    def test_normalize_recent_apps_non_list_returns_empty(self):
        assert config_mod._normalize_recent_apps(None) == []
        assert config_mod._normalize_recent_apps("not a list") == []
        assert config_mod._normalize_recent_apps({}) == []

    def test_normalize_recent_apps_drops_non_dict_entries(self):
        raw = [
            "string_entry",
            {"desktop_id": "good.desktop", "last_closed": 1000},
        ]
        result = config_mod._normalize_recent_apps(raw)
        assert result == [{"desktop_id": "good.desktop", "last_closed": 1000}]

    def test_normalize_recent_apps_drops_entries_without_valid_desktop_id(self):
        raw = [
            {"desktop_id": "", "last_closed": 1000},
            {"desktop_id": 123, "last_closed": 1000},
            {"last_closed": 1000},
            {"desktop_id": "ok.desktop", "last_closed": 500},
        ]
        result = config_mod._normalize_recent_apps(raw)
        assert result == [{"desktop_id": "ok.desktop", "last_closed": 500}]

    def test_normalize_recent_apps_drops_entries_without_valid_last_closed(self):
        raw = [
            {"desktop_id": "a.desktop", "last_closed": "string_time"},
            {"desktop_id": "b.desktop"},
            {"desktop_id": "c.desktop", "last_closed": 1.5},
        ]
        result = config_mod._normalize_recent_apps(raw)
        assert result == [{"desktop_id": "c.desktop", "last_closed": 1}]


class TestConfigSaveEdgeCases:
    def test_save_cleanup_oserror_when_unlink_fails(self, tmp_path, monkeypatch):
        path = tmp_path / "dock.json"
        config = Config(icon_size=96)

        def _patched_write_json_atomic_candidate(*, path, payload):
            path.write_text("{}")
            raise OSError("disk full")

        monkeypatch.setattr(
            config_mod,
            "_write_json_atomic_candidate",
            _patched_write_json_atomic_candidate,
        )
        # Also make unlink fail so the cleanup handler fires
        monkeypatch.setattr(
            config_mod.Path,
            "unlink",
            lambda _self, **kw: (_ for _ in ()).throw(OSError("unlink failed")),
        )
        unlink_errors: list[str] = []

        class _Capture:
            def warning(self, msg, *args):
                unlink_errors.append(msg % args)

        monkeypatch.setattr(config_mod, "logger", _Capture())

        with pytest.raises(OSError):
            config.save(path)

        assert any(
            "Failed to clean up temporary config file" in e for e in unlink_errors
        )

    def test_backup_cleanup_oserror_when_unlink_fails(self, tmp_path, monkeypatch):
        source = tmp_path / "source.json"
        backup = tmp_path / "backup.json"
        source.write_text("{}", encoding="utf-8")
        unlink_errors: list[str] = []

        class _Capture:
            def warning(self, msg, *args):
                unlink_errors.append(msg % args)

        monkeypatch.setattr(config_mod, "logger", _Capture())

        # Make read_bytes raise so _write_backup_copy enters the except block
        def _fail_read_bytes(_self):
            raise OSError("disk full")

        monkeypatch.setattr(config_mod.Path, "read_bytes", _fail_read_bytes)
        # Make unlink also fail so the cleanup handler fires
        monkeypatch.setattr(
            config_mod.Path,
            "unlink",
            lambda _self, **kw: (_ for _ in ()).throw(OSError("unlink failed")),
        )

        with pytest.raises(OSError):
            config_mod._write_backup_copy(source=source, backup_path=backup)

        assert any(
            "Failed to clean up temporary backup file" in e for e in unlink_errors
        )


class TestConfigHelpers:
    def test_pinned_entry_equality_and_raw_shapes(self, tmp_path):
        folder_uri = tmp_path.as_uri()
        applet_id = "applet://clock"
        entry = PinnedEntry(kind=APP_KIND, target="firefox.desktop")

        assert entry == "firefox.desktop"
        assert entry == {"kind": APP_KIND, "target": "firefox.desktop"}
        assert entry != 1
        assert PinnedEntry.from_raw(entry) is entry
        assert PinnedEntry.from_raw("") is None
        assert PinnedEntry.from_raw(applet_id) == PinnedEntry(
            kind=APPLET_KIND,
            target=applet_id,
        )
        assert PinnedEntry.from_raw(folder_uri) == PinnedEntry(
            kind=FOLDER_KIND,
            target=folder_uri,
        )
        assert PinnedEntry.from_raw({"kind": APP_KIND, "target": ""}) is None
        assert PinnedEntry.from_raw({"kind": "bad", "target": "x"}) is None
        assert PinnedEntry.from_raw(["bad"]) is None

    def test_resolve_initial_desktop_id_uses_candidates_and_fallbacks(self):
        assert (
            config_mod._resolve_initial_desktop_id(
                candidates=("bad.desktop", "good.desktop"),
                fallback_content_types=(),
                desktop_id_exists=lambda desktop_id: desktop_id == "good.desktop",
                default_desktop_id_for=lambda _content_type: None,
            )
            == "good.desktop"
        )

        assert (
            config_mod._resolve_initial_desktop_id(
                candidates=(),
                fallback_content_types=("text/plain",),
                desktop_id_exists=lambda _desktop_id: True,
                default_desktop_id_for=lambda content_type: (
                    "fallback.desktop" if content_type == "text/plain" else None
                ),
            )
            == "fallback.desktop"
        )

    def test_build_initial_pinned_suppresses_duplicate_callback_results(self):
        available = {"firefox.desktop", "gnome-terminal.desktop"}
        defaults = {
            "inode/directory": "firefox.desktop",
            "text/plain": "gnome-terminal.desktop",
            "x-scheme-handler/mailto": "firefox.desktop",
        }

        pinned = config_mod.build_initial_pinned(
            desktop_id_exists=available.__contains__,
            default_desktop_id_for=defaults.get,
        )

        assert pinned == [
            PinnedEntry(kind=APPLET_KIND, target="applet://applications"),
            PinnedEntry(kind=APP_KIND, target="firefox.desktop"),
            PinnedEntry(kind=APP_KIND, target="gnome-terminal.desktop"),
            PinnedEntry(kind=APPLET_KIND, target="applet://clock"),
            PinnedEntry(kind=APPLET_KIND, target="applet://calendar"),
            PinnedEntry(kind=APPLET_KIND, target="applet://weather"),
            PinnedEntry(kind=APPLET_KIND, target="applet://systemmonitor"),
            PinnedEntry(kind=APPLET_KIND, target="applet://hydration"),
            PinnedEntry(kind=APPLET_KIND, target="applet://notifications"),
            PinnedEntry(kind=APPLET_KIND, target="applet://session"),
        ]

    def test_read_config_data_rejects_non_object(self, tmp_path):
        path = tmp_path / "dock.json"
        path.write_text("[]", encoding="utf-8")

        with pytest.raises(ValueError):
            config_mod._read_config_data(path=path)

        assert config_mod._is_valid_config_file(path=path) is False

    def test_write_backup_copy_cleans_temp_on_failure(self, tmp_path, monkeypatch):
        source = tmp_path / "source.json"
        backup = tmp_path / "backup.json"
        source.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(
            Path, "read_bytes", lambda _self: (_ for _ in ()).throw(OSError("boom"))
        )

        with pytest.raises(OSError):
            config_mod._write_backup_copy(source=source, backup_path=backup)

        assert not backup.exists()
