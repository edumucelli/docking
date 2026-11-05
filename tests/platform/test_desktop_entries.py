"""Tests for shared desktop-entry helpers."""

from __future__ import annotations

import logging
import stat
from pathlib import Path

from docking.platform.applications.implementations.unix import (
    entries as desktop_entries,
)


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
        key_file = desktop_entries.load_desktop_key_file(generated.path)
        assert key_file is not None
        exec_line = desktop_entries.desktop_entry_string(key_file, "Exec")
        assert (
            desktop_entries.executable_path_from_exec_line(exec_line)
            == binary.resolve()
        )


class TestExecParsingFallbacks:
    """Exec lines in the wild are often malformed; parsing must not raise."""

    def test_unterminated_quote_yields_no_basename(self):
        assert desktop_entries.normalized_exec_basename('"/usr/bin/app --flag') == ""

    def test_whitespace_only_exec_yields_nothing(self):
        assert desktop_entries.normalized_exec_basename("   ") == ""
        assert desktop_entries.executable_path_from_exec_line("   ") is None

    def test_unterminated_quote_yields_no_path(self):
        assert desktop_entries.executable_path_from_exec_line('"/usr/bin/app') is None


class TestWineAliasRecovery:
    def test_unterminated_quote_falls_back_to_token_scan(self):
        aliases = desktop_entries.wine_executable_aliases(
            'wine "C:\\Games\\App\\Tool.exe'
        )

        assert aliases == ["tool.exe", "tool"]

    def test_match_aliases_includes_wine_aliases(self):
        aliases = desktop_entries.match_aliases(
            "wine-program.desktop", "Wine", 'wine "/home/u/Games/Tool.exe"'
        )

        assert {"wine-program", "tool.exe", "tool"} <= set(aliases)

    def test_match_aliases_without_wine_uses_the_exec_basename(self):
        aliases = desktop_entries.match_aliases("editor.desktop", "", "/usr/bin/editor")

        assert "editor" in aliases


class TestDesktopDirs:
    def test_user_dirs_precede_system_and_host_dirs(self, tmp_path, monkeypatch):
        data_home = tmp_path / "data"
        (data_home / "applications").mkdir(parents=True)
        share = tmp_path / "share"
        (share / "applications").mkdir(parents=True)
        host = tmp_path / "host"
        (host / "applications").mkdir(parents=True)

        monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
        monkeypatch.setenv("XDG_DATA_DIRS", f"{share}:/does/not/exist")
        monkeypatch.setattr(desktop_entries, "HOST_XDG_DATA_DIRS", (str(host),))
        monkeypatch.setattr(desktop_entries, "is_flatpak", lambda: False)

        assert desktop_entries.desktop_dirs() == [
            data_home / "applications",
            share / "applications",
            host / "applications",
        ]

    def test_absent_directories_are_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "absent"))
        monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "also-absent"))
        monkeypatch.setattr(desktop_entries, "HOST_XDG_DATA_DIRS", ())
        monkeypatch.setattr(desktop_entries, "is_flatpak", lambda: False)

        assert desktop_entries.desktop_dirs() == []

    def test_flatpak_prepends_the_host_user_directory(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        host_user_apps = home / ".local" / "share" / "applications"
        host_user_apps.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "empty"))
        monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "none"))
        monkeypatch.setattr(desktop_entries, "HOST_XDG_DATA_DIRS", ())
        monkeypatch.setattr(desktop_entries, "is_flatpak", lambda: True)

        assert desktop_entries.desktop_dirs() == [host_user_apps]


class TestHostDesktopFile:
    def test_run_host_prefix_counts_as_host_outside_flatpak(self):
        path = Path("/run/host/usr/share/applications/app.desktop")

        assert desktop_entries.is_host_desktop_file(path) is True
        assert desktop_entries.is_host_desktop_file(None) is False

    def test_plain_paths_are_not_host_outside_flatpak(self, tmp_path, monkeypatch):
        monkeypatch.setattr(desktop_entries, "is_flatpak", lambda: False)

        assert desktop_entries.is_host_desktop_file(tmp_path / "app.desktop") is False

    def test_flatpak_treats_user_and_snap_dirs_as_host(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".local" / "share" / "applications").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setattr(desktop_entries, "is_flatpak", lambda: True)

        assert (
            desktop_entries.is_host_desktop_file(
                home / ".local" / "share" / "applications" / "app.desktop"
            )
            is True
        )
        assert (
            desktop_entries.is_host_desktop_file(
                Path("/var/lib/snapd/desktop/applications/app.desktop")
            )
            is True
        )
        assert (
            desktop_entries.is_host_desktop_file(tmp_path / "elsewhere" / "a.desktop")
            is False
        )


class TestDesktopFileActionExec:
    def test_reads_the_named_action_exec_line(self, tmp_path):
        path = tmp_path / "app.desktop"
        path.write_text(
            "[Desktop Entry]\nType=Application\nName=App\nExec=app\n\n"
            "[Desktop Action open-special]\nName=Open\nExec=app --special %U\n",
            encoding="utf-8",
        )

        assert (
            desktop_entries.desktop_file_action_exec(path, "open-special")
            == "app --special %U"
        )

    def test_missing_action_or_unparsable_file_returns_empty(self, tmp_path):
        path = tmp_path / "app.desktop"
        path.write_text(
            "[Desktop Entry]\nType=Application\nName=App\nExec=app\n",
            encoding="utf-8",
        )

        assert desktop_entries.desktop_file_action_exec(path, "missing") == ""
        assert (
            desktop_entries.desktop_file_action_exec(tmp_path / "nope.desktop", "x")
            == ""
        )


class TestLocalPathResolution:
    def test_empty_and_remote_targets_have_no_local_path(self):
        assert desktop_entries.local_path_from_uri_or_path("") is None
        assert (
            desktop_entries.local_path_from_uri_or_path("https://example.com/a.png")
            is None
        )

    def test_file_uris_are_unquoted(self):
        assert desktop_entries.local_path_from_uri_or_path("file:///tmp/x%20y") == Path(
            "/tmp/x y"
        )


class TestMakeUserExecutable:
    def test_missing_file_reports_failure_without_raising(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="docking.desktop_entries"):
            result = desktop_entries.make_user_executable(tmp_path / "missing")

        assert result is False
        assert "Failed to make file executable" in caplog.text


class TestGeneratedEntryWriteFailure:
    def test_failed_rename_warns_and_cleans_up_its_temp_file(
        self, tmp_path, monkeypatch, caplog
    ):
        binary = tmp_path / "tool"
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        _make_executable(binary)

        apps = tmp_path / "data" / "applications"
        apps.mkdir(parents=True)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setattr(
            desktop_entries, "_refresh_desktop_database", lambda _d: None
        )
        # Occupying the destination with a directory makes the atomic rename
        # fail, which is the realistic way this write can go wrong.
        desktop_id = desktop_entries.generated_desktop_id_for_path(binary)
        (apps / desktop_id).mkdir()

        with caplog.at_level(logging.WARNING, logger="docking.desktop_entries"):
            generated = desktop_entries.create_desktop_entry_for_executable(binary)

        assert generated is None
        assert "Failed to write generated desktop entry" in caplog.text
        assert not [entry for entry in apps.iterdir() if entry.name.endswith(".tmp")]


class TestDesktopDatabaseRefresh:
    def test_refresh_shells_out_to_update_desktop_database(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            desktop_entries.subprocess,
            "Popen",
            lambda argv, **kwargs: calls.append((argv, kwargs)),
        )

        desktop_entries._refresh_desktop_database(tmp_path)

        assert calls[0][0] == ["update-desktop-database", str(tmp_path)]
        assert calls[0][1]["start_new_session"] is True

    def test_refresh_is_silent_when_the_tool_is_absent(self, tmp_path, monkeypatch):
        def raise_missing(*_args, **_kwargs):
            raise OSError("not found")

        monkeypatch.setattr(desktop_entries.subprocess, "Popen", raise_missing)

        desktop_entries._refresh_desktop_database(tmp_path)


class TestGeneratedEntryNaming:
    def test_non_appimage_suffix_is_dropped_from_the_display_name(
        self, tmp_path, monkeypatch
    ):
        binary = tmp_path / "tool.bin"
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        _make_executable(binary)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setattr(
            desktop_entries, "_refresh_desktop_database", lambda _d: None
        )

        generated = desktop_entries.create_desktop_entry_for_executable(binary)

        assert generated is not None
        assert generated.name == "tool"

    def test_name_that_slugs_to_nothing_still_produces_a_launcher(
        self, tmp_path, monkeypatch
    ):
        binary = tmp_path / "___"
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        _make_executable(binary)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setattr(
            desktop_entries, "_refresh_desktop_database", lambda _d: None
        )

        generated = desktop_entries.create_desktop_entry_for_executable(binary)

        assert generated is not None
        assert generated.desktop_id.startswith("docking-generated-launcher-")


def test_windows_document_drop_never_generates_a_unix_shell_launcher(
    tmp_path, monkeypatch
):
    document = tmp_path / "notes.txt"
    document.write_text("Windows reports X_OK for existing ordinary files")
    from docking.platform.applications.implementations.windows.registration import (
        WindowsExecutableRegistration,
    )

    backend = WindowsExecutableRegistration(tmp_path / "launchers")
    monkeypatch.setattr(
        desktop_entries, "user_applications_dir", lambda: tmp_path / "launchers"
    )
    assert backend.register(document.as_uri()) is None
    assert not (tmp_path / "launchers").exists()
