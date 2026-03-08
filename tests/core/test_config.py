"""Tests for config loading, saving, and defaults."""

import json

from docking.core.config import APP_KIND, FILE_KIND, FOLDER_KIND, Config, PinnedEntry
from docking.core.position import Position


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
        assert c.monitor_index == -1
        assert c.autohide is False
        assert c.hide_delay_ms == 0
        assert c.previews_enabled is True
        assert c.tooltips_enabled is True
        assert c.theme == "default"
        assert isinstance(c.pinned, list)

    def test_previews_enabled_default_true(self):
        # Given / When
        c = Config()
        # Then
        assert c.previews_enabled is True

    def test_hide_delay_default_zero(self):
        # Given / When
        c = Config()
        # Then
        assert c.hide_delay_ms == 0

    def test_pos_property_returns_position_enum(self):
        c = Config(position="left")
        assert c.pos is Position.LEFT


class TestConfigLoad:
    def test_load_missing_file_creates_default(self, tmp_path):
        # Given
        path = tmp_path / "dock.json"
        # When
        config = Config.load(path)
        # Then
        assert config.icon_size == 48
        assert path.exists()

    def test_load_previews_enabled(self, tmp_path):
        # Given
        path = tmp_path / "dock.json"
        data = {"previews_enabled": False}
        path.write_text(json.dumps(data))
        # When
        config = Config.load(path)
        # Then
        assert config.previews_enabled is False

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
        assert config.autohide is False

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

    def test_load_normalizes_bool_like_values(self, tmp_path):
        path = tmp_path / "dock.json"
        path.write_text(
            json.dumps(
                {
                    "autohide": 1,
                    "previews_enabled": "false",
                    "tooltips_enabled": "yes",
                    "zoom_enabled": 0,
                }
            )
        )

        config = Config.load(path)

        assert config.autohide is True
        assert config.previews_enabled is False
        assert config.tooltips_enabled is True
        assert config.zoom_enabled is False

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
