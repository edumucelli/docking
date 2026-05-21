"""Tests for theme loading, scaling unit system, and color parsing."""

import json
import logging
from unittest.mock import patch

import pytest

import docking.core.theme.migration as theme_migration_mod
from docking.core.theme import (
    _BUILTIN_THEMES_DIR,
    _USER_THEME_TEMPLATE_NAME,
    DEPRECATED_THEME_KEYS,
    Theme,
    _rgba,
    ensure_user_theme_template,
    list_theme_names,
    migrate_theme_dict,
    user_themes_dir,
)


class TestRgba:
    def test_white_opaque(self):
        # Given / When
        result = _rgba(values=[255, 255, 255, 255])
        # Then
        assert result == pytest.approx((1.0, 1.0, 1.0, 1.0))

    def test_black_transparent(self):
        # Given / When
        result = _rgba(values=[0, 0, 0, 0])
        # Then
        assert result == pytest.approx((0.0, 0.0, 0.0, 0.0))

    def test_mid_values(self):
        # Given / When
        r, g, b, a = _rgba(values=[128, 64, 32, 200])
        # Then
        assert r == pytest.approx(128 / 255)
        assert g == pytest.approx(64 / 255)
        assert b == pytest.approx(32 / 255)
        assert a == pytest.approx(200 / 255)


class TestThemeDefaults:
    def test_default_theme_has_valid_colors(self):
        # Given / When
        t = Theme()
        # Then
        assert len(t.fill_start) == 4
        assert all(0 <= c <= 1 for c in t.fill_start)
        assert t.roundness > 0
        assert t.indicator_radius > 0


class TestThemeLoad:
    def test_load_default_theme(self):
        # Given / When
        t = Theme.load("default", 48)
        # Then
        assert t.roundness == 5.0
        assert t.stroke_width == 1.0

    def test_load_missing_theme_returns_defaults(self):
        # Given / When
        t = Theme.load("nonexistent-theme-name", 48)
        # Then
        assert t == Theme()

    def test_load_partial_theme(self, tmp_path):
        """Theme file with only some keys -- rest use defaults."""
        # Given
        theme_data = {"roundness": 16, "stroke_width": 2.0}
        theme_file = tmp_path / "custom.json"
        theme_file.write_text(json.dumps(theme_data))
        # When
        with patch("docking.core.theme.theme._BUILTIN_THEMES_DIR", tmp_path):
            t = Theme.load("custom", 48)
        # Then
        assert t.roundness == 16.0
        assert t.stroke_width == 2.0
        # Defaults for unspecified
        assert t.indicator_radius == 2.5

    def test_loads_user_theme_from_config_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        directory = user_themes_dir()
        directory.mkdir(parents=True)
        (directory / "custom.json").write_text(
            json.dumps({"roundness": 14, "stroke_width": 3.0}),
            encoding="utf-8",
        )

        t = Theme.load("custom", 48)

        assert t.roundness == 14.0
        assert t.stroke_width == 3.0

    def test_user_theme_overrides_builtin_theme(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        directory = user_themes_dir()
        directory.mkdir(parents=True)
        (directory / "default.json").write_text(
            json.dumps({"roundness": 22}),
            encoding="utf-8",
        )

        t = Theme.load("default", 48)

        assert t.roundness == 22.0

    def test_user_theme_template_is_created_but_not_listed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

        names = list_theme_names()

        template = user_themes_dir() / f"{_USER_THEME_TEMPLATE_NAME}.json"
        template_data = json.loads(template.read_text(encoding="utf-8"))
        assert template.exists()
        assert template_data["layout"]["horizontal_padding"] == 0
        assert not (set(template_data) & set(DEPRECATED_THEME_KEYS))
        assert _USER_THEME_TEMPLATE_NAME not in names
        assert "default" in names

    def test_load_creates_user_theme_template(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

        Theme.load("default", 48)

        template = user_themes_dir() / f"{_USER_THEME_TEMPLATE_NAME}.json"
        assert template.exists()

    def test_existing_user_theme_template_is_migrated(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        template = user_themes_dir() / f"{_USER_THEME_TEMPLATE_NAME}.json"
        template.parent.mkdir(parents=True)
        template.write_text('{"roundness": 33}\n', encoding="utf-8")

        ensure_user_theme_template()

        data = json.loads(template.read_text(encoding="utf-8"))
        backup = theme_migration_mod._theme_migration_backup_path(template)
        assert data["shelf"]["corner_radius_px"] == 33
        assert "roundness" not in data
        assert backup.exists()
        assert json.loads(backup.read_text(encoding="utf-8")) == {"roundness": 33}

    @pytest.mark.parametrize(
        ("theme_name", "expected_roundness"),
        [
            ("nord", 6.0),
            ("gruvbox", 6.0),
            ("solarized", 5.0),
            ("paper", 16.0),
            ("candy", 18.0),
            ("pill", 999.0),
        ],
    )
    def test_load_new_builtin_themes(self, theme_name, expected_roundness):
        t = Theme.load(theme_name, 48)
        assert t.roundness == expected_roundness
        if theme_name in {"paper", "candy", "pill"}:
            assert t.stroke_width == pytest.approx(0.8)
        else:
            assert t.stroke_width == pytest.approx(1.0)
        assert all(0 <= c <= 1 for c in t.fill_start)
        assert all(0 <= c <= 1 for c in t.fill_end)
        assert all(0 <= c <= 1 for c in t.stroke)

    def test_pill_theme_is_floating_and_fully_rounded(self):
        t = Theme.load("pill", 48)

        assert t.round_bottom is True
        assert t.distance_from_edge == 6
        assert t.roundness > t.shelf_height / 2
        assert t.fill_start[3] > 0.9

    def test_builtin_themes_use_current_schema(self):
        for theme_file in _BUILTIN_THEMES_DIR.glob("*.json"):
            data = json.loads(theme_file.read_text(encoding="utf-8"))
            assert not (set(data) & set(DEPRECATED_THEME_KEYS)), theme_file.name

            theme = Theme.load(theme_file.stem, 48)
            assert all(0 <= c <= 1 for c in theme.fill_start)
            assert all(0 <= c <= 1 for c in theme.fill_end)
            assert all(0 <= c <= 1 for c in theme.stroke)

    def test_load_final_nested_theme_schema(self, tmp_path):
        theme_data = {
            "shelf": {
                "fill_start_color": [1, 2, 3, 128],
                "fill_end_color": [4, 5, 6, 129],
                "stroke_color": [7, 8, 9, 130],
                "stroke_width_px": 2.0,
                "inner_stroke_color": [10, 11, 12, 131],
                "corner_radius_px": 11,
                "round_bottom": True,
            },
            "layout": {
                "horizontal_padding": 3,
                "top_padding": 2,
                "bottom_padding": 1.5,
                "item_padding": 2.5,
                "distance_from_edge_px": 6,
            },
            "indicators": {
                "inactive_color": [13, 14, 15, 132],
                "active_color": [16, 17, 18, 133],
                "size_px": 7,
                "style": "dashes",
                "max_dots": 9,
            },
            "items": {
                "hover": {
                    "lighten_amount": 0.33,
                    "fade_ms": 222,
                },
                "bounce": {
                    "urgent_height_ratio": 1.2,
                    "launch_height_ratio": 0.4,
                    "urgent_time_ms": 700,
                    "launch_time_ms": 800,
                    "click_time_ms": 250,
                },
                "glow": {
                    "opacity_ratio": 0.44,
                    "urgent_time_ms": 9000,
                    "urgent_pulse_ms": 1200,
                    "urgent_size_ratio": 0.7,
                },
            },
        }
        theme_file = tmp_path / "nested.json"
        theme_file.write_text(json.dumps(theme_data), encoding="utf-8")

        with patch("docking.core.theme.theme._BUILTIN_THEMES_DIR", tmp_path):
            t = Theme.load("nested", 48)

        assert t.fill_start == pytest.approx((1 / 255, 2 / 255, 3 / 255, 128 / 255))
        assert t.fill_end == pytest.approx((4 / 255, 5 / 255, 6 / 255, 129 / 255))
        assert t.stroke == pytest.approx((7 / 255, 8 / 255, 9 / 255, 130 / 255))
        assert t.stroke_width == pytest.approx(2.0)
        assert t.inner_stroke == pytest.approx(
            (10 / 255, 11 / 255, 12 / 255, 131 / 255)
        )
        assert t.roundness == 11.0
        assert t.round_bottom is True
        assert t.horizontal_padding == pytest.approx(14.4)
        assert t.top_padding == pytest.approx(9.6)
        assert t.bottom_padding == pytest.approx(7.2)
        assert t.item_padding == pytest.approx(12.0)
        assert t.distance_from_edge == 6
        assert t.indicator_color == pytest.approx(
            (13 / 255, 14 / 255, 15 / 255, 132 / 255)
        )
        assert t.active_indicator_color == pytest.approx(
            (16 / 255, 17 / 255, 18 / 255, 133 / 255)
        )
        assert t.indicator_radius == pytest.approx(3.5)
        assert t.indicator_style.value == "dashes"
        assert t.max_indicator_dots == 9
        assert t.hover_lighten == pytest.approx(0.33)
        assert t.active_time_ms == 222
        assert t.urgent_bounce_height == pytest.approx(1.2)
        assert t.launch_bounce_height == pytest.approx(0.4)
        assert t.urgent_bounce_time_ms == 700
        assert t.launch_bounce_time_ms == 800
        assert t.click_time_ms == 250
        assert t.glow_opacity == pytest.approx(0.44)
        assert t.urgent_glow_time_ms == 9000
        assert t.urgent_glow_pulse_ms == 1200
        assert t.urgent_glow_size == pytest.approx(0.7)

    def test_partial_nested_theme_uses_defaults(self, tmp_path):
        theme_data = {
            "shelf": {"fill_start_color": [1, 2, 3, 4]},
            "layout": {},
            "items": {"hover": {"fade_ms": 200}},
        }
        theme_file = tmp_path / "partial.json"
        theme_file.write_text(json.dumps(theme_data), encoding="utf-8")

        with patch("docking.core.theme.theme._BUILTIN_THEMES_DIR", tmp_path):
            t = Theme.load("partial", 48)

        assert t.fill_start == pytest.approx((1 / 255, 2 / 255, 3 / 255, 4 / 255))
        assert t.fill_end == pytest.approx((30 / 255, 30 / 255, 30 / 255, 220 / 255))
        assert t.horizontal_padding == pytest.approx(2.0)
        assert t.item_padding == pytest.approx(12.0)
        assert t.active_time_ms == 200
        assert t.hover_lighten == pytest.approx(0.2)


class TestThemeMigration:
    def test_migrate_theme_dict_is_idempotent_without_registered_keys(self):
        data = {"roundness": 5, "meta": {"author": "custom"}}

        result = migrate_theme_dict(data, deprecated_keys={})

        assert result.changed is False
        assert result.data == data
        assert result.data is not data

    def test_migrate_theme_dict_moves_legacy_key_and_preserves_unknowns(self):
        data = {
            "legacy_padding": 3,
            "roundness": 5,
            "metadata": {"author": "custom"},
        }

        result = migrate_theme_dict(
            data,
            deprecated_keys={"legacy_padding": "layout.horizontal_padding"},
        )

        assert result.changed is True
        assert "legacy_padding" not in result.data
        assert result.data["layout"]["horizontal_padding"] == 3
        assert result.data["roundness"] == 5
        assert result.data["metadata"] == {"author": "custom"}
        assert "legacy_padding" in data

    def test_migrate_theme_dict_prefers_new_value_when_both_exist(self):
        data = {
            "legacy_padding": 3,
            "layout": {"horizontal_padding": 7},
        }

        result = migrate_theme_dict(
            data,
            deprecated_keys={"legacy_padding": "layout.horizontal_padding"},
        )

        assert "legacy_padding" not in result.data
        assert result.data["layout"]["horizontal_padding"] == 7
        assert result.changes[0].conflict is True
        assert "ignored" in result.warnings[0]

    def test_migrate_theme_dict_replaces_malformed_nested_section(self):
        data = {"legacy_padding": 3, "layout": None}

        result = migrate_theme_dict(
            data,
            deprecated_keys={"legacy_padding": "layout.horizontal_padding"},
        )

        assert result.data["layout"] == {"horizontal_padding": 3}
        assert "not an object" in result.warnings[0]

    def test_theme_load_rewrites_migrated_user_theme_and_creates_backup(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setattr(
            theme_migration_mod,
            "DEPRECATED_THEME_KEYS",
            {
                "legacy_padding": "layout.horizontal_padding",
                "roundness": "shelf.corner_radius_px",
            },
        )
        directory = user_themes_dir()
        directory.mkdir(parents=True)
        theme_file = directory / "custom.json"
        original = {"roundness": 14, "legacy_padding": 3}
        theme_file.write_text(json.dumps(original), encoding="utf-8")

        theme = Theme.load("custom", 48)

        migrated = json.loads(theme_file.read_text(encoding="utf-8"))
        backup = theme_migration_mod._theme_migration_backup_path(theme_file)
        assert theme.roundness == 14.0
        assert migrated["layout"]["horizontal_padding"] == 3
        assert "legacy_padding" not in migrated
        assert backup.exists()
        assert json.loads(backup.read_text(encoding="utf-8")) == original

    def test_theme_migration_backup_is_not_listed_as_theme(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        directory = user_themes_dir()
        directory.mkdir(parents=True)
        theme_file = directory / "custom.json"
        theme_file.write_text("{}", encoding="utf-8")
        backup = theme_migration_mod._theme_migration_backup_path(theme_file)
        backup.write_text("{}", encoding="utf-8")

        names = list_theme_names()

        assert "custom" in names
        assert backup.name not in names

    def test_theme_load_uses_in_memory_migration_when_rewrite_fails(
        self, tmp_path, monkeypatch, caplog
    ):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setattr(
            theme_migration_mod,
            "DEPRECATED_THEME_KEYS",
            {"legacy_roundness": "shelf.corner_radius_px"},
        )

        def raise_permission(**_kwargs):
            raise PermissionError("read-only")

        monkeypatch.setattr(
            theme_migration_mod,
            "_write_theme_json_atomic",
            raise_permission,
        )
        directory = user_themes_dir()
        directory.mkdir(parents=True)
        theme_file = directory / "custom.json"
        original = {"legacy_roundness": 19}
        theme_file.write_text(json.dumps(original), encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="docking.theme"):
            theme = Theme.load("custom", 48)

        assert theme.roundness == 19.0
        assert json.loads(theme_file.read_text(encoding="utf-8")) == original
        assert "Failed to rewrite migrated user theme" in caplog.text

    def test_flat_user_theme_migrates_to_final_nested_schema(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        directory = user_themes_dir()
        directory.mkdir(parents=True)
        theme_file = directory / "flat.json"
        theme_file.write_text(
            json.dumps(
                {
                    "fill_start": [1, 2, 3, 4],
                    "stroke_width": 2.0,
                    "roundness": 10,
                    "indicator_color": [5, 6, 7, 8],
                    "indicator_size": 9,
                    "top_padding": 2,
                    "urgent_bounce_height": 1.2,
                    "active_time_ms": 210,
                    "glow_opacity": 0.5,
                    "round_bottom": True,
                    "distance_from_edge": 6,
                }
            ),
            encoding="utf-8",
        )

        t = Theme.load("flat", 48)

        migrated = json.loads(theme_file.read_text(encoding="utf-8"))
        assert t.fill_start == pytest.approx((1 / 255, 2 / 255, 3 / 255, 4 / 255))
        assert t.stroke_width == pytest.approx(2.0)
        assert t.roundness == 10.0
        assert t.indicator_color == pytest.approx((5 / 255, 6 / 255, 7 / 255, 8 / 255))
        assert t.indicator_radius == pytest.approx(4.5)
        assert t.top_padding == pytest.approx(9.6)
        assert t.urgent_bounce_height == pytest.approx(1.2)
        assert t.active_time_ms == 210
        assert t.glow_opacity == pytest.approx(0.5)
        assert t.round_bottom is True
        assert t.distance_from_edge == 6
        assert "fill_start" not in migrated
        assert "stroke_width" not in migrated
        assert "indicator_color" not in migrated
        assert migrated["shelf"]["fill_start_color"] == [1, 2, 3, 4]
        assert migrated["shelf"]["stroke_width_px"] == 2.0
        assert migrated["shelf"]["corner_radius_px"] == 10
        assert migrated["shelf"]["round_bottom"] is True
        assert migrated["layout"]["top_padding"] == 2
        assert migrated["layout"]["distance_from_edge_px"] == 6
        assert migrated["indicators"]["inactive_color"] == [5, 6, 7, 8]
        assert migrated["indicators"]["size_px"] == 9
        assert migrated["items"]["bounce"]["urgent_height_ratio"] == 1.2
        assert migrated["items"]["hover"]["fade_ms"] == 210
        assert migrated["items"]["glow"]["opacity_ratio"] == 0.5

    def test_final_nested_value_wins_over_legacy_flat_value(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        directory = user_themes_dir()
        directory.mkdir(parents=True)
        theme_file = directory / "mixed.json"
        theme_file.write_text(
            json.dumps(
                {
                    "fill_start": [1, 1, 1, 255],
                    "shelf": {"fill_start_color": [2, 3, 4, 255]},
                }
            ),
            encoding="utf-8",
        )

        t = Theme.load("mixed", 48)

        migrated = json.loads(theme_file.read_text(encoding="utf-8"))
        assert t.fill_start == pytest.approx((2 / 255, 3 / 255, 4 / 255, 1.0))
        assert "fill_start" not in migrated
        assert migrated["shelf"]["fill_start_color"] == [2, 3, 4, 255]


class TestScalingUnit:
    """Tests for the scaling unit system: JSON values * (icon_size / 10)."""

    def test_default_48px_item_padding(self):
        # Given
        # scale = 48/10 = 4.8, so 2.5 * 4.8 = 12.0
        t = Theme.load("default", 48)
        # Then
        assert t.item_padding == pytest.approx(12.0)

    def test_default_48px_top_padding(self):
        # Given
        # scale = 4.8, so -7 * 4.8 = -33.6
        t = Theme.load("default", 48)
        # Then
        assert t.top_padding == pytest.approx(-33.6)

    def test_default_48px_bottom_padding(self):
        # Given
        # scale = 4.8, so 1 * 4.8 = 4.8
        t = Theme.load("default", 48)
        # Then
        assert t.bottom_padding == pytest.approx(4.8)

    def test_default_64px_scales_proportionally(self):
        # Given
        t48 = Theme.load("default", 48)
        t64 = Theme.load("default", 64)
        # Then
        ratio = 64 / 48
        assert t64.item_padding == pytest.approx(t48.item_padding * ratio, rel=1e-6)
        assert t64.top_padding == pytest.approx(t48.top_padding * ratio, rel=1e-6)
        assert t64.bottom_padding == pytest.approx(t48.bottom_padding * ratio, rel=1e-6)

    def test_nord_64px_scales_proportionally(self):
        t48 = Theme.load("nord", 48)
        t64 = Theme.load("nord", 64)

        ratio = 64 / 48
        assert t64.item_padding == pytest.approx(t48.item_padding * ratio, rel=1e-6)
        assert t64.top_padding == pytest.approx(t48.top_padding * ratio, rel=1e-6)
        assert t64.bottom_padding == pytest.approx(t48.bottom_padding * ratio, rel=1e-6)

    def test_horizontal_padding_fallback_when_zero(self):
        # Given
        # When horizontal_padding <= 0, fallback = 2 * stroke_width = 2.0
        t = Theme.load("default", 48)
        # Then
        assert t.horizontal_padding == pytest.approx(2.0)

    def test_legacy_h_padding_migrates_to_nested_horizontal_padding(
        self, tmp_path, monkeypatch
    ):
        # Given
        # 3 * 4.8 = 14.4 > 0, so no fallback
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        directory = user_themes_dir()
        directory.mkdir(parents=True)
        theme_data = {"h_padding": 3, "stroke_width": 1.0}
        theme_file = directory / "pos.json"
        theme_file.write_text(json.dumps(theme_data), encoding="utf-8")
        # When
        t = Theme.load("pos", 48)
        # Then
        migrated = json.loads(theme_file.read_text(encoding="utf-8"))
        assert t.horizontal_padding == pytest.approx(14.4)
        assert migrated["layout"]["horizontal_padding"] == 3
        assert "h_padding" not in migrated

    def test_nested_horizontal_padding_uses_scaled(self, tmp_path):
        # Given
        theme_data = {"layout": {"horizontal_padding": 3}, "stroke_width": 1.0}
        theme_file = tmp_path / "pos.json"
        theme_file.write_text(json.dumps(theme_data))
        # When
        with patch("docking.core.theme.theme._BUILTIN_THEMES_DIR", tmp_path):
            t = Theme.load("pos", 48)
        # Then
        assert t.horizontal_padding == pytest.approx(14.4)

    def test_nested_horizontal_padding_wins_over_legacy(self, tmp_path, monkeypatch):
        # Given
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        directory = user_themes_dir()
        directory.mkdir(parents=True)
        theme_file = directory / "custom.json"
        theme_file.write_text(
            json.dumps(
                {
                    "h_padding": 1,
                    "layout": {"horizontal_padding": 3},
                    "stroke_width": 1.0,
                }
            ),
            encoding="utf-8",
        )
        # When
        t = Theme.load("custom", 48)
        # Then
        migrated = json.loads(theme_file.read_text(encoding="utf-8"))
        assert t.horizontal_padding == pytest.approx(14.4)
        assert migrated["layout"]["horizontal_padding"] == 3
        assert "h_padding" not in migrated

    def test_indicator_radius_from_size(self):
        # Given
        # indicator_radius = 5 / 2 = 2.5 (NOT scaled)
        t = Theme.load("default", 48)
        # Then
        assert t.indicator_radius == pytest.approx(2.5)


class TestShelfHeightDerivation:
    """shelf_height = max(0, icon_size + top_offset + bottom_offset)

    top_offset = 2 * stroke_width + top_padding_px
    bottom_offset = bottom_padding_px
    """

    def test_default_48px_shelf_height(self):
        # Given
        # top_padding_px = -7 * 4.8 = -33.6
        # bottom_padding_px = 1 * 4.8 = 4.8
        # top_offset = 2 * 1.0 + (-33.6) = -31.6
        # bottom_offset = 4.8
        # shelf_height = max(0, 48 + (-31.6) + 4.8) = 21.2
        t = Theme.load("default", 48)
        # Then
        assert t.shelf_height == pytest.approx(21.2)

    def test_default_64px_shelf_height(self):
        # Given
        # scale = 6.4
        # top_padding_px = -7 * 6.4 = -44.8
        # bottom_padding_px = 1 * 6.4 = 6.4
        # top_offset = 2 * 1.0 + (-44.8) = -42.8
        # bottom_offset = 6.4
        # shelf_height = max(0, 64 + (-42.8) + 6.4) = 27.6
        t = Theme.load("default", 64)
        # Then
        assert t.shelf_height == pytest.approx(27.6)

    def test_shelf_height_never_negative(self, tmp_path):
        # Given
        theme_data = {"top_padding": -20, "bottom_padding": -5}
        theme_file = tmp_path / "neg.json"
        theme_file.write_text(json.dumps(theme_data))
        # When
        with patch("docking.core.theme.theme._BUILTIN_THEMES_DIR", tmp_path):
            t = Theme.load("neg", 48)
        # Then
        assert t.shelf_height >= 0.0


class TestAnimationParams:
    """Animation params are loaded directly, NOT scaled."""

    def test_default_bounce_heights(self):
        t = Theme.load("default", 48)
        assert t.urgent_bounce_height == pytest.approx(1.66)
        assert t.launch_bounce_height == pytest.approx(0.625)

    def test_default_durations(self):
        t = Theme.load("default", 48)
        assert t.urgent_bounce_time_ms == 600
        assert t.launch_bounce_time_ms == 600
        assert t.click_time_ms == 300
        assert t.active_time_ms == 150

    def test_default_visual_params(self):
        t = Theme.load("default", 48)
        assert t.hover_lighten == pytest.approx(0.2)
        assert t.max_indicator_dots == 4
        assert t.glow_opacity == pytest.approx(0.6)

    def test_animation_params_same_at_different_icon_sizes(self):
        # Given
        t48 = Theme.load("default", 48)
        t64 = Theme.load("default", 64)
        # Then
        assert t48.urgent_bounce_height == t64.urgent_bounce_height
        assert t48.launch_bounce_time_ms == t64.launch_bounce_time_ms
        assert t48.hover_lighten == t64.hover_lighten
        assert t48.glow_opacity == t64.glow_opacity

    def test_missing_animation_keys_use_defaults(self, tmp_path):
        # Given
        theme_data = {"roundness": 5}
        theme_file = tmp_path / "minimal.json"
        theme_file.write_text(json.dumps(theme_data))
        # When
        with patch("docking.core.theme.theme._BUILTIN_THEMES_DIR", tmp_path):
            t = Theme.load("minimal", 48)
        # Then
        assert t.urgent_bounce_height == pytest.approx(1.66)
        assert t.launch_bounce_height == pytest.approx(0.625)
        assert t.click_time_ms == 300
        assert t.hover_lighten == pytest.approx(0.2)
        assert t.max_indicator_dots == 4
        assert t.glow_opacity == pytest.approx(0.6)


class TestThemeOpacity:
    def test_with_opacity_scales_rgba_alpha_only(self):
        theme = Theme(
            fill_start=(0.1, 0.2, 0.3, 0.8),
            fill_end=(0.4, 0.5, 0.6, 0.7),
            stroke=(0.2, 0.3, 0.4, 0.6),
            inner_stroke=(0.7, 0.8, 0.9, 0.5),
            indicator_color=(0.3, 0.4, 0.5, 0.9),
            active_indicator_color=(0.6, 0.5, 0.4, 1.0),
            glow_opacity=0.6,
        )

        scaled = theme.with_opacity(0.5)

        assert scaled.fill_start == pytest.approx((0.1, 0.2, 0.3, 0.4))
        assert scaled.fill_end == pytest.approx((0.4, 0.5, 0.6, 0.35))
        assert scaled.stroke == pytest.approx((0.2, 0.3, 0.4, 0.3))
        assert scaled.inner_stroke == pytest.approx((0.7, 0.8, 0.9, 0.25))
        assert scaled.indicator_color == pytest.approx((0.3, 0.4, 0.5, 0.45))
        assert scaled.active_indicator_color == pytest.approx((0.6, 0.5, 0.4, 0.5))
        assert scaled.glow_opacity == pytest.approx(theme.glow_opacity)
        assert scaled.indicator_radius == pytest.approx(theme.indicator_radius)
