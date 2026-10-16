"""Emptying Trash must preserve files outside it and metadata for failed items."""

import os

import pytest

from docking.applets.trash import backend


@pytest.fixture
def trash_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(backend, "is_flatpak", lambda: False)
    mount = tmp_path / "volume"
    mount.mkdir()
    monkeypatch.setattr(backend, "_mount_points", lambda: (mount,))
    return mount


@pytest.mark.parametrize("factory", [backend.MateTrashBackend, backend.KdeTrashBackend])
@pytest.mark.parametrize("link", ["root", "files", "shared"])
def test_empty_does_not_follow_volume_trash_links(
    tmp_path, trash_environment, factory, link
):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("not trashed")
    mount = trash_environment
    uid = str(os.getuid())
    if link == "root":
        (outside / "files").mkdir()
        sentinel.rename(outside / "files" / sentinel.name)
        sentinel = outside / "files" / sentinel.name
        (mount / f".Trash-{uid}").symlink_to(outside, target_is_directory=True)
    elif link == "files":
        trash = mount / f".Trash-{uid}"
        trash.mkdir()
        (trash / "files").symlink_to(outside, target_is_directory=True)
    else:
        files = outside / uid / "files"
        files.mkdir(parents=True)
        sentinel.rename(files / sentinel.name)
        sentinel = files / sentinel.name
        (mount / ".Trash").symlink_to(outside, target_is_directory=True)

    factory()._delete_contents()

    assert sentinel.read_text() == "not trashed"


@pytest.mark.parametrize("factory", [backend.MateTrashBackend, backend.KdeTrashBackend])
@pytest.mark.parametrize("kind", ["file", "dangling-link"])
@pytest.mark.parametrize("mode", [0o500, 0o000])
@pytest.mark.skipif(
    os.geteuid() == 0, reason="requires unprivileged filesystem permissions"
)
def test_failed_deletion_keeps_its_restore_metadata(
    tmp_path, trash_environment, factory, kind, mode
):
    trash = tmp_path / "data" / "Trash"
    files, info = trash / "files", trash / "info"
    files.mkdir(parents=True)
    info.mkdir()
    remaining = files / "report.txt"
    if kind == "file":
        remaining.write_text("still recoverable")
    else:
        remaining.symlink_to(tmp_path / "missing")
    metadata = info / "report.txt.trashinfo"
    metadata.write_text("[Trash Info]\nPath=/original/report.txt\n")
    files.chmod(mode)
    try:
        result = factory()._delete_contents()
    finally:
        files.chmod(0o700)

    assert result.permission_denied
    if kind == "file":
        assert remaining.read_text() == "still recoverable"
    else:
        assert remaining.is_symlink()
    assert metadata.read_text() == "[Trash Info]\nPath=/original/report.txt\n"


@pytest.mark.parametrize(
    "invalid", ["file", "writable", "unowned", "shared-not-sticky"]
)
def test_volume_trash_discovery_rejects_invalid_directories(
    trash_environment, monkeypatch, invalid
):
    uid = os.getuid()
    if invalid == "unowned":
        monkeypatch.setattr(backend.os, "getuid", lambda: uid + 1)
        path = trash_environment / f".Trash-{uid + 1}"
        path.mkdir()
    elif invalid == "shared-not-sticky":
        path = trash_environment / ".Trash" / str(uid)
        path.mkdir(parents=True)
    else:
        path = trash_environment / f".Trash-{uid}"
        if invalid == "file":
            path.write_text("not a directory")
        else:
            path.mkdir()
            path.chmod(0o777)

    assert backend._volume_trash_directories() == ()


@pytest.mark.parametrize("factory", [backend.MateTrashBackend, backend.KdeTrashBackend])
@pytest.mark.parametrize("target_exists", [False, True])
def test_unsafe_files_directory_keeps_restore_metadata(
    tmp_path, trash_environment, factory, target_exists
):
    trash = tmp_path / "data" / "Trash"
    info = trash / "info"
    info.mkdir(parents=True)
    outside = tmp_path / "outside"
    if target_exists:
        outside.mkdir()
    (trash / "files").symlink_to(outside, target_is_directory=True)
    metadata = info / "report.txt.trashinfo"
    metadata.write_text("[Trash Info]\nPath=/original/report.txt\n")

    result = factory()._delete_contents()

    assert result.failures
    assert metadata.read_text() == "[Trash Info]\nPath=/original/report.txt\n"
