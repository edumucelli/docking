"""Focused tests for file and URI target services."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import docking.platform.desktop.implementations.unix.targets as native_targets
from tests.platform_factories import make_icon_loader, make_target_service

try:
    import gi  # noqa: F401
except Exception:
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.platform import icons as icons_mod
from docking.platform import targets as targets_mod
from docking.platform.icons import FileIconInfo
from docking.platform.targets import (
    FileTargetInfo,
    normalize_file_target,
    open_target,
)


@pytest.fixture(autouse=True)
def use_gio_default_handler(monkeypatch):
    """These shared tests exercise the non-Windows Gio/Flatpak launch path."""
    from docking.platform.desktop.implementations.unix.backend import (
        UnixDesktopServices,
    )

    monkeypatch.setattr(targets_mod, "desktop_services", UnixDesktopServices)


def test_default_uri_launch_ownership_is_centralized():
    package = Path(__file__).resolve().parents[2] / "docking"
    allowed = {
        Path("platform/desktop/implementations/unix/targets.py"),
        Path("platform/targets.py"),
        Path("platform/applications/implementations/unix/entries.py"),
        Path("platform/applications/launcher.py"),
        Path("platform/applications/registry.py"),
        Path("applets/music/applet.py"),
        Path("search/services/recent_files.py"),
    }
    direct_launches: set[Path] = set()

    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "launch_default_for_uri"
            for node in ast.walk(tree)
        ):
            direct_launches.add(path.relative_to(package))

    assert direct_launches <= allowed
    assert {Path("platform/desktop/implementations/unix/targets.py")} <= direct_launches
    assert Path("search/services/recent_files.py") not in direct_launches


def test_normalize_file_target_accepts_local_path_and_file_uri(tmp_path):
    path = tmp_path / "hello world.txt"
    path.write_text("hello")
    expected = path.resolve().as_uri()

    assert normalize_file_target(str(path)) == expected
    assert normalize_file_target(path.as_uri()) == expected


@pytest.mark.parametrize("authority", ["localhost", "LOCALHOST"])
def test_file_uri_localhost_authority_keeps_local_target(tmp_path, authority):
    path = tmp_path / "hello world.txt"
    path.write_text("hello")
    uri_path = path.as_uri().removeprefix("file://")

    assert normalize_file_target(f"file://{authority}{uri_path}") == path.as_uri()


@pytest.mark.skipif(os.name == "nt", reason="Windows file authorities are UNC paths")
@pytest.mark.parametrize("authority", ["server.example", "127.0.0.1"])
def test_remote_file_uri_never_opens_same_local_path(tmp_path, monkeypatch, authority):
    path = tmp_path / "report.txt"
    path.write_text("local file with the same path")
    target = f"file://{authority}{path.as_posix()}"
    launch = MagicMock()
    monkeypatch.setattr(targets_mod.Gio.AppInfo, "launch_default_for_uri", launch)

    assert normalize_file_target(target) is None
    assert open_target(target) is False
    launch.assert_not_called()


def test_symlink_loop_fails_without_launching_or_raising(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.symlink_to(second)
    second.symlink_to(first)
    launch = MagicMock()
    monkeypatch.setattr(targets_mod.Gio.AppInfo, "launch_default_for_uri", launch)

    assert normalize_file_target(str(first)) is None
    assert open_target(first.as_uri()) is False
    launch.assert_not_called()


@pytest.mark.parametrize(
    "target",
    ("", "https://example.com", "mailto:test@example.com", "ftp://example.com"),
)
def test_normalize_file_target_rejects_non_file_targets(target):
    assert normalize_file_target(target) is None


def test_open_target_accepts_local_path_and_file_uri(tmp_path, monkeypatch):
    path = tmp_path / "example.txt"
    path.write_text("hello")
    launch = MagicMock()
    monkeypatch.setattr(native_targets, "is_flatpak", lambda: False)
    monkeypatch.setattr(targets_mod.Gio.AppInfo, "launch_default_for_uri", launch)

    assert open_target(str(path)) is True
    assert open_target(path.as_uri()) is True

    assert [call.args for call in launch.call_args_list] == [
        (path.resolve().as_uri(), None),
        (path.resolve().as_uri(), None),
    ]


@pytest.mark.parametrize(
    "target",
    (
        "http://example.com",
        "https://example.com/docs",
        "mailto:test@example.com",
    ),
)
def test_open_target_accepts_supported_non_file_uri(target, monkeypatch):
    launch = MagicMock()
    monkeypatch.setattr(targets_mod.Gio.AppInfo, "launch_default_for_uri", launch)

    assert open_target(target) is True

    launch.assert_called_once_with(target, None)


@pytest.mark.parametrize(
    "target",
    (
        "ftp://example.com",
        "trash:///",
        "docking-preview:document/42",
        "custom+desktop://resource/42",
    ),
)
def test_target_service_rejects_unsupported_uri_scheme(target, monkeypatch):
    launch = MagicMock()
    monkeypatch.setattr(targets_mod.Gio.AppInfo, "launch_default_for_uri", launch)
    service = make_target_service(icon_loader=MagicMock())

    assert service.open_target(target) is False

    launch.assert_not_called()


def test_open_target_normalizes_relative_path(tmp_path, monkeypatch):
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    launch = MagicMock()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(native_targets, "is_flatpak", lambda: False)
    monkeypatch.setattr(targets_mod.Gio.AppInfo, "launch_default_for_uri", launch)

    assert open_target("notes.txt") is True

    launch.assert_called_once_with(path.as_uri(), None)


@pytest.mark.parametrize(
    "target",
    (
        "https://[broken",
        "file://",
        "\0invalid-path",
    ),
)
def test_open_target_rejects_malformed_targets(target, monkeypatch):
    launch = MagicMock()
    monkeypatch.setattr(targets_mod.Gio.AppInfo, "launch_default_for_uri", launch)

    assert open_target(target) is False

    launch.assert_not_called()


def test_open_target_uses_flatpak_host_gio_for_local_file(tmp_path, monkeypatch):
    path = tmp_path / "host.txt"
    path.write_text("hello")
    uri = path.resolve().as_uri()
    host_command = MagicMock(
        return_value=["/usr/bin/flatpak-spawn", "--host", "gio", "open", uri]
    )
    popen = MagicMock()
    launch = MagicMock()
    monkeypatch.setattr(native_targets, "is_flatpak", lambda: True)
    monkeypatch.setattr(native_targets.flatpak, "host_command", host_command)
    monkeypatch.setattr(native_targets.subprocess, "Popen", popen)
    monkeypatch.setattr(targets_mod.Gio.AppInfo, "launch_default_for_uri", launch)

    assert open_target(str(path)) is True

    host_command.assert_called_once_with(["gio", "open", uri])
    popen.assert_called_once_with(
        ["/usr/bin/flatpak-spawn", "--host", "gio", "open", uri],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    launch.assert_not_called()


def test_open_target_falls_back_after_host_spawn_error(tmp_path, monkeypatch):
    path = tmp_path / "host.txt"
    path.write_text("hello")
    uri = path.resolve().as_uri()
    launch = MagicMock()
    monkeypatch.setattr(native_targets, "is_flatpak", lambda: True)
    monkeypatch.setattr(
        native_targets.flatpak,
        "host_command",
        lambda _argv: ["/usr/bin/flatpak-spawn", "--host", "gio", "open", uri],
    )
    monkeypatch.setattr(
        native_targets.subprocess,
        "Popen",
        MagicMock(side_effect=OSError("spawn failed")),
    )
    monkeypatch.setattr(targets_mod.Gio.AppInfo, "launch_default_for_uri", launch)

    assert open_target(str(path)) is True

    launch.assert_called_once_with(uri, None)


def test_open_target_returns_false_for_gio_error(monkeypatch):
    launch = MagicMock()
    monkeypatch.setattr(targets_mod.Gio.AppInfo, "launch_default_for_uri", launch)

    monkeypatch.setattr(targets_mod.GLib, "Error", RuntimeError)
    launch.side_effect = RuntimeError("no handler")
    assert open_target("https://example.com") is False

    launch.assert_called_once_with("https://example.com", None)


@pytest.mark.parametrize(
    ("target", "display_name", "file_type", "content_type", "icon_name", "is_dir"),
    (
        (
            "/tmp/report.txt",
            "",
            targets_mod.Gio.FileType.REGULAR,
            "text/plain",
            "text-x-generic",
            False,
        ),
        (
            "/tmp/Documents",
            "Documents",
            targets_mod.Gio.FileType.DIRECTORY,
            "inode/directory",
            "folder",
            True,
        ),
    ),
)
def test_resolve_file_preserves_target_metadata(
    target,
    display_name,
    file_type,
    content_type,
    icon_name,
    is_dir,
    monkeypatch,
):
    gicon = MagicMock()
    info = MagicMock()
    info.get_display_name.return_value = display_name
    info.get_icon.return_value = gicon
    info.get_file_type.return_value = file_type
    info.get_content_type.return_value = content_type
    gfile = MagicMock()
    gfile.query_info.return_value = info
    monkeypatch.setattr(targets_mod, "file_for_uri", lambda _uri: gfile)
    icon_loader = MagicMock()
    icon_loader.resolve_file_icon_info.return_value = FileIconInfo("pixbuf")
    service = make_target_service(icon_loader=icon_loader)
    uri = normalize_file_target(target)
    assert uri is not None

    result = service.resolve_file(target, 48)

    assert result == FileTargetInfo(
        target=uri,
        name=display_name or target.rsplit("/", 1)[-1],
        icon_name=icon_name,
        icon="pixbuf",
        is_dir=is_dir,
    )
    gfile.query_info.assert_called_once_with(
        "standard::display-name,standard::icon,standard::type,standard::content-type",
        targets_mod.Gio.FileQueryInfoFlags.NONE,
        None,
    )
    icon_loader.resolve_file_icon_info.assert_called_once_with(
        target=uri,
        gicon=gicon,
        content_type=content_type,
        size=48,
        is_dir=is_dir,
        thumbnail_size=None,
    )


def test_resolve_file_returns_none_for_query_error(monkeypatch):
    monkeypatch.setattr(targets_mod.GLib, "Error", RuntimeError)
    gfile = MagicMock()
    gfile.query_info.side_effect = RuntimeError("missing")
    monkeypatch.setattr(targets_mod, "file_for_uri", lambda _uri: gfile)

    assert (
        make_target_service(icon_loader=MagicMock()).resolve_file("/tmp/missing", 48)
        is None
    )


def test_resolve_file_uses_image_thumbnail(tmp_path, monkeypatch):
    path = tmp_path / "photo.png"
    path.write_bytes(b"image")
    thumbnail = object()
    pixbuf_cls = MagicMock()
    pixbuf_cls.new_from_file_at_scale.return_value = thumbnail
    monkeypatch.setattr(
        icons_mod.GdkPixbuf,
        "Pixbuf",
        pixbuf_cls,
        raising=False,
    )
    info = MagicMock()
    info.get_display_name.return_value = "Photo"
    info.get_icon.return_value = MagicMock()
    info.get_file_type.return_value = targets_mod.Gio.FileType.REGULAR
    info.get_content_type.return_value = "image/png"
    gfile = MagicMock()
    gfile.query_info.return_value = info
    monkeypatch.setattr(targets_mod, "file_for_uri", lambda _uri: gfile)

    result = make_target_service(icon_loader=make_icon_loader()).resolve_file(
        str(path), 48
    )

    assert result is not None
    assert result.icon is thumbnail
    assert result.is_thumbnail is True
    pixbuf_cls.new_from_file_at_scale.assert_called_once_with(
        str(path.resolve()),
        48,
        48,
        True,
    )


def test_default_directory_handler_name_and_pending():
    from docking.platform.applications.associations import (
        AssociationResolution,
        AssociationStatus,
    )

    registry = MagicMock()
    service = make_target_service(
        icon_loader=MagicMock(), application_registry=registry
    )
    registry.directory_application_name.return_value = AssociationResolution(
        AssociationStatus.READY, name="Files"
    )
    assert service.default_directory_app_name() == "Files"
    registry.directory_application_name.assert_called_once_with()
    for status in (AssociationStatus.PENDING, AssociationStatus.UNAVAILABLE):
        registry.directory_application_name.return_value = AssociationResolution(status)
        assert service.default_directory_app_name() is None
