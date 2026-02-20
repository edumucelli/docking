"""Tests for config loading, saving, and defaults."""

import json
import pytest
from pathlib import Path

from docking.core.config import Config


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
        assert c.autohide is False
        assert c.theme == "default"
        assert isinstance(c.pinned, list)


class TestConfigLoad:
    def test_load_missing_file_creates_default(self, tmp_path):
        # Given
        path = tmp_path / "dock.json"
        # When
        config = Config.load(path)
        # Then
        assert config.icon_size == 48
        assert path.exists()

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
        assert config.pinned == ["foo.desktop"]
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
            icon_size=80, zoom_percent=1.5, pinned=["a.desktop", "b.desktop"]
        )
        # When
        original.save(path)
        loaded = Config.load(path)
        # Then
        assert loaded.icon_size == 80
        assert loaded.zoom_percent == 1.5
        assert loaded.pinned == ["a.desktop", "b.desktop"]
