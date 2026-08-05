"""Tests for shared desktop-entry helpers."""

from __future__ import annotations

import stat
from types import SimpleNamespace

from docking.platform import desktop_entries


def _make_executable(path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class TestExecutablePath:
    def test_extracts_canonical_absolute_exec_path(self, tmp_path):
        executable = tmp_path / "app" / "bin" / "tool"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"\x7fELF")

        result = desktop_entries.executable_path_from_exec_line(
            f'"{executable}" --flag %U'
        )

        assert result == executable.resolve()

    def test_rejects_path_resolved_and_missing_commands(self, tmp_path):
        assert desktop_entries.executable_path_from_exec_line("tool --flag") is None
        assert (
            desktop_entries.executable_path_from_exec_line(str(tmp_path / "missing"))
            is None
        )


class TestWineDesktopAliases:
    def test_wine_executable_aliases_extract_windows_path(self):
        aliases = desktop_entries.wine_executable_aliases(
            'env WINEPREFIX="/home/user/.wine" wine '
            '"C:\\Program Files\\Starcraft\\Starcraft.exe"'
        )

        assert aliases == ["starcraft.exe", "starcraft"]

    def test_wine_executable_aliases_extract_unix_path(self):
        aliases = desktop_entries.wine_executable_aliases(
            'wine "/home/user/Games/App/app.exe" --flag'
        )

        assert aliases == ["app.exe", "app"]

    def test_wine_executable_aliases_ignores_non_wine_exec(self):
        assert desktop_entries.wine_executable_aliases("mono /tmp/tool.exe") == []

    def test_desktop_info_replaces_generic_wine_startup_class(self, tmp_path):
        desktop_file = tmp_path / "wine-program.desktop"
        desktop_file.write_text(
            "\n".join(
                [
                    "[Desktop Entry]",
                    "Type=Application",
                    "Name=Wine Program",
                    'Exec=env WINEPREFIX="/home/user/.wine" wine "C:\\\\App\\\\Tool.exe"',
                    "StartupWMClass=Wine",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        info = desktop_entries.desktop_info_from_file(
            desktop_id="wine-program.desktop",
            path=desktop_file,
        )

        assert info is not None
        assert info.wm_class == "tool.exe"
        assert desktop_entries.desktop_match_aliases(info) == [
            "tool.exe",
            "wine-program",
            "tool",
        ]

    def test_desktop_info_replaces_lowercase_generic_wine_startup_class(self, tmp_path):
        desktop_file = tmp_path / "wine-program.desktop"
        desktop_file.write_text(
            "\n".join(
                [
                    "[Desktop Entry]",
                    "Type=Application",
                    "Name=Wine Program",
                    'Exec=wine "C:\\\\App\\\\Tool.exe"',
                    "StartupWMClass=wine",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        info = desktop_entries.desktop_info_from_file(
            desktop_id="wine-program.desktop",
            path=desktop_file,
        )

        assert info is not None
        assert info.wm_class == "tool.exe"


class TestGeneratedDesktopEntries:
    def test_appimage_path_creates_stable_desktop_id(self, tmp_path, monkeypatch):
        appimage = tmp_path / "PrusaSlicer.AppImage"
        appimage.write_text("#!/bin/sh\n", encoding="utf-8")
        _make_executable(appimage)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setattr(
            desktop_entries, "_refresh_desktop_database", lambda _d: None
        )

        first = desktop_entries.create_desktop_entry_for_executable(appimage)
        second = desktop_entries.create_desktop_entry_for_executable(appimage.as_uri())

        assert first is not None
        assert second is not None
        assert first.desktop_id == second.desktop_id
        assert first.desktop_id.startswith("docking-generated-prusaslicer-")
        assert first.desktop_id.endswith(".desktop")

    def test_generated_file_contains_marker_and_source_path(
        self, tmp_path, monkeypatch
    ):
        binary = tmp_path / "my tool"
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        _make_executable(binary)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setattr(
            desktop_entries, "_refresh_desktop_database", lambda _d: None
        )

        generated = desktop_entries.create_desktop_entry_for_executable(binary)

        assert generated is not None
        text = generated.path.read_text(encoding="utf-8")
        assert "Type=Application\n" in text
        assert "Name=my tool\n" in text
        assert f'Exec="{binary.resolve()}"\n' in text
        assert "X-Docking-Generated=true\n" in text
        assert f"X-Docking-Source-Path={binary.resolve()}\n" in text

        info = desktop_entries.desktop_info_from_file(
            desktop_id=generated.desktop_id,
            path=generated.path,
        )
        assert info is not None
        assert info.name == "my tool"
        assert info.icon_name == "application-x-executable"
        assert info.exec_line == f'"{binary.resolve()}"'

    def test_generated_file_can_preserve_runtime_wm_class(self, tmp_path, monkeypatch):
        binary = tmp_path / "tool"
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        _make_executable(binary)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setattr(
            desktop_entries, "_refresh_desktop_database", lambda _d: None
        )

        generated = desktop_entries.create_desktop_entry_for_executable(
            binary,
            startup_wm_class="SharedTool",
        )

        assert generated is not None
        text = generated.path.read_text(encoding="utf-8")
        assert "StartupWMClass=SharedTool\n" in text

    def test_generated_file_uses_icon_next_to_executable(self, tmp_path, monkeypatch):
        binary = tmp_path / "tool"
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        _make_executable(binary)
        icon = tmp_path / "tool.svg"
        icon.write_text("<svg/>", encoding="utf-8")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setattr(
            desktop_entries, "_refresh_desktop_database", lambda _d: None
        )

        generated = desktop_entries.create_desktop_entry_for_executable(binary)

        assert generated is not None
        text = generated.path.read_text(encoding="utf-8")
        assert f"Icon={icon.resolve()}\n" in text

    def test_existing_generated_app_info_recovers_sibling_icon(self, tmp_path):
        binary = tmp_path / "tool"
        binary.write_bytes(b"\x7fELF")
        icon_path = tmp_path / "tool.svg"
        icon_path.write_text("<svg/>", encoding="utf-8")
        fallback_icon = SimpleNamespace(to_string=lambda: desktop_entries.FALLBACK_ICON)
        app_info = SimpleNamespace(
            get_icon=lambda: fallback_icon,
            get_commandline=lambda: str(binary),
            get_startup_wm_class=lambda: "SharedTool",
            get_display_name=lambda: "Tool",
        )

        info = desktop_entries.desktop_info_from_app_info(
            desktop_id="docking-generated-tool-123.desktop",
            app_info=app_info,
        )

        assert info.icon_name == str(icon_path.resolve())

    def test_repeated_generation_is_idempotent(self, tmp_path, monkeypatch):
        binary = tmp_path / "tool"
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        _make_executable(binary)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setattr(
            desktop_entries, "_refresh_desktop_database", lambda _d: None
        )

        first = desktop_entries.create_desktop_entry_for_executable(binary)
        second = desktop_entries.create_desktop_entry_for_executable(binary)

        assert first is not None
        assert second is not None
        assert first.path == second.path
        assert first.path.read_text(encoding="utf-8") == second.path.read_text(
            encoding="utf-8"
        )

    def test_non_executable_regular_file_is_rejected(self, tmp_path, monkeypatch):
        file_path = tmp_path / "notes.txt"
        file_path.write_text("hello", encoding="utf-8")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

        generated = desktop_entries.create_desktop_entry_for_executable(file_path)

        assert generated is None
        assert not (tmp_path / "data" / "applications").exists()

    def test_non_executable_appimage_can_be_detected_and_marked_executable(
        self, tmp_path
    ):
        appimage = tmp_path / "GIMP.AppImage"
        appimage.write_text("#!/bin/sh\n", encoding="utf-8")
        appimage.chmod(0o644)

        detected = desktop_entries.appimage_path_needing_executable_permission(
            appimage.as_uri()
        )

        assert detected == appimage.resolve()
        assert desktop_entries.make_user_executable(detected)
        assert detected.stat().st_mode & stat.S_IXUSR
        assert (
            desktop_entries.appimage_path_needing_executable_permission(
                appimage.as_uri()
            )
            is None
        )

    def test_directories_and_non_local_uris_are_rejected(self, tmp_path):
        assert desktop_entries.create_desktop_entry_for_executable(tmp_path) is None
        assert (
            desktop_entries.create_desktop_entry_for_executable(
                "https://example.com/tool.AppImage"
            )
            is None
        )

    def test_exec_path_escapes_quotes_and_backslashes(self, tmp_path, monkeypatch):
        binary = tmp_path / 'my "tool" \\ runner'
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        _make_executable(binary)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setattr(
            desktop_entries, "_refresh_desktop_database", lambda _d: None
        )

        generated = desktop_entries.create_desktop_entry_for_executable(binary)

        assert generated is not None
        text = generated.path.read_text(encoding="utf-8")
        assert 'Exec="' in text
        assert '\\"tool\\"' in text
        assert "\\\\ runner" in text
