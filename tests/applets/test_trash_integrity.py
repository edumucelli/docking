"""Emptying Trash must preserve files outside it and metadata for failed items."""

import os

import pytest

from docking.applets.trash.implementations.unix import backend


def test_shared_trash_operations_require_a_counter():
    class MissingCounter(backend.UnixTrashWatch, backend.TrashBackend):
        name = "incomplete"
        uri = "trash:///"

        def open(self):
            pass

        def empty(self, confirm):
            raise AssertionError("not called")

    with pytest.raises(TypeError, match="count_items"):
        MissingCounter()


@pytest.mark.parametrize("factory", [backend.MateTrashBackend, backend.KdeTrashBackend])
@pytest.mark.parametrize("swap", ["child", "root"])
def test_directory_swap_during_deletion_never_follows_symlink(
    tmp_path, monkeypatch, factory, swap
):
    trash = tmp_path / "Trash"
    files = trash / "files"
    child = files / "folder"
    child.mkdir(parents=True)
    (child / "trashed.txt").write_text("discard")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep")
    original_open = os.open
    swapped = False

    def interleaved_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == ("folder" if swap == "child" else "files") and not swapped:
            swapped = True
            target = child if swap == "child" else files
            target.rename(target.with_name(target.name + "-moved"))
            target.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", interleaved_open)
    result = factory()._delete_directory_contents(files)

    assert swapped and result.failures
    assert sentinel.read_text() == "keep"


@pytest.fixture
def trash_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(backend, "is_flatpak", lambda: False)
    mount = tmp_path / "volume"
    mount.mkdir()
    monkeypatch.setattr(backend, "_mount_points", lambda: (mount,))
    return mount


@pytest.mark.parametrize("factory", [backend.MateTrashBackend, backend.KdeTrashBackend])
@pytest.mark.parametrize(
    "host_succeeds,stats,failures,permission_denied",
    [
        (True, None, 0, False),
        (False, None, 0, False),
        (False, backend._DeleteStats(failures=2), 2, False),
        (False, backend._DeleteStats(failures=3, permission_denied=True), 3, True),
    ],
)
def test_empty_result_recounts_after_deletion(
    tmp_path,
    trash_environment,
    monkeypatch,
    factory,
    host_succeeds,
    stats,
    failures,
    permission_denied,
):
    files = tmp_path / "data" / "Trash" / "files"
    files.mkdir(parents=True)
    deleted = files / "deleted.txt"
    deleted.touch()
    (files / "remaining.txt").touch()
    selected = factory()
    monkeypatch.setattr(selected, "_confirmation_preference", lambda: False)

    def empty_host():
        if host_succeeds:
            deleted.unlink()
        return host_succeeds

    def delete_contents():
        deleted.unlink()
        return stats

    monkeypatch.setattr(backend, "_empty_host_trash", empty_host)
    monkeypatch.setattr(selected, "_delete_contents", delete_contents)

    result = selected.empty(lambda: pytest.fail("confirmation is disabled"))

    assert result.before_count == 2
    assert result.remaining_count == 1
    assert result.attempted is True
    assert result.delegated is False
    assert result.failures == failures
    assert result.permission_denied is permission_denied


@pytest.mark.parametrize("factory", [backend.MateTrashBackend, backend.KdeTrashBackend])
def test_empty_attempts_all_files_before_restore_metadata(
    tmp_path, trash_environment, monkeypatch, factory
):
    home = tmp_path / "data" / "Trash"
    volume = trash_environment / f".Trash-{os.getuid()}"
    for root, name in ((home, "home.txt"), (volume, "volume.txt")):
        (root / "files").mkdir(parents=True)
        root.chmod(0o700)
        (root / "info").mkdir()
        (root / "files" / name).touch()
        (root / "info" / f"{name}.trashinfo").write_text("[Trash Info]")
    attempts = []
    delete_entry = backend._delete_entry

    def record_delete(parent_fd, name):
        attempts.append(name)
        return delete_entry(parent_fd, name)

    monkeypatch.setattr(backend, "_delete_entry", record_delete)

    result = factory()._delete_contents()

    assert result.failures == 0
    assert attempts == [
        "home.txt",
        "volume.txt",
        "home.txt.trashinfo",
        "volume.txt.trashinfo",
    ]


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
