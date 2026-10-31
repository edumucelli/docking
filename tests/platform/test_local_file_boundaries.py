"""File consumers preserve authority and reject unusable paths."""

import os
from pathlib import Path

import pytest

from docking.applets.dragshare.state import file_path_from_uri
from docking.core.config import Config
from docking.core.icons import CUSTOM_ICON_PATH_KEY, ICON_SOURCE_PREF_KEY, IconSource
from docking.core.items import DockItem
from docking.platform.applications.entries import (
    desktop_id_from_uri_or_path,
    local_path_from_uri_or_path,
)
from docking.platform.icon_overrides import custom_icon_path
from docking.platform.icons import IconLoader, _normalize_file_target_for_icon
from docking.platform.targets import normalize_file_target
from docking.search.preview import (
    local_path_from_target,
    preview_local_descriptor,
    preview_local_target,
)

LOCAL_CONSUMERS = [
    normalize_file_target,
    _normalize_file_target_for_icon,
    local_path_from_uri_or_path,
    desktop_id_from_uri_or_path,
    local_path_from_target,
    file_path_from_uri,
]


@pytest.mark.parametrize("consume", LOCAL_CONSUMERS)
@pytest.mark.parametrize(
    "target",
    [
        pytest.param(
            "file://server.example/tmp/test.desktop",
            marks=pytest.mark.skipif(
                os.name == "nt", reason="Windows supports UNC file targets"
            ),
        ),
        "file://[broken",
        "file://localhost",
        "file:report.desktop",
        "file:../report.desktop",
        "file:%2Ftmp%2Freport.desktop",
    ],
)
def test_nonlocal_or_malformed_file_uri_is_not_a_local_path(consume, target):
    assert consume(target) is None


@pytest.mark.parametrize("consume", LOCAL_CONSUMERS)
def test_other_user_home_follows_platform_expansion_without_raising(consume):
    expected = None
    if os.name == "nt":
        expected = Path.home().parent / "_docking_audit_absent_user" / "test.desktop"
        if consume is desktop_id_from_uri_or_path:
            expected = expected.name
        elif consume in (normalize_file_target, _normalize_file_target_for_icon):
            expected = expected.as_uri()
    assert consume("~_docking_audit_absent_user/test.desktop") == expected


@pytest.mark.skipif(os.name == "nt", reason="Windows supports UNC file targets")
@pytest.mark.parametrize("preview", [preview_local_target, preview_local_descriptor])
def test_remote_file_preview_does_not_read_the_same_local_path(tmp_path, preview):
    path = tmp_path / "private.txt"
    path.write_text("local-only audit sentinel")
    target = f"file://server.example{path.as_posix()}"

    result = preview(target=target, title="Remote document")

    assert result.kind == "uri"
    assert "local-only audit sentinel" not in result.body


@pytest.mark.parametrize("preview", [preview_local_target, preview_local_descriptor])
def test_relative_file_uri_does_not_resolve_against_working_directory(
    tmp_path, monkeypatch, preview
):
    (tmp_path / "report.txt").write_text("working-directory audit sentinel")
    monkeypatch.chdir(tmp_path)

    result = preview(target="file:report.txt", title="Invalid file URI")

    assert result.kind == "uri"
    assert "working-directory audit sentinel" not in result.body


def test_broken_symlink_cannot_abort_thumbnail_lookup(tmp_path):
    path = tmp_path / "loop.png"
    path.symlink_to(path.name)

    assert _normalize_file_target_for_icon(str(path)) is None


@pytest.mark.parametrize("consume", LOCAL_CONSUMERS)
@pytest.mark.skipif(os.name == "nt", reason="POSIX byte filenames")
def test_file_uri_preserves_non_utf8_filename_bytes(tmp_path, consume):
    path = tmp_path / os.fsdecode(b"audit-\xff.desktop")
    path.write_text("[Desktop Entry]\nType=Application\nName=Audit\nExec=true\n")
    expected = path
    if consume is desktop_id_from_uri_or_path:
        expected = path.name
    elif consume in (normalize_file_target, _normalize_file_target_for_icon):
        expected = path.as_uri()

    assert consume(path.as_uri()) == expected


def test_stale_custom_icon_home_does_not_abort_item_loading():
    item = DockItem(desktop_id="audit.desktop", target="audit.desktop")
    config = Config(
        item_prefs={
            "audit.desktop": {
                ICON_SOURCE_PREF_KEY: IconSource.CUSTOM.value,
                CUSTOM_ICON_PATH_KEY: "~_docking_audit_absent_user/icon.png",
            }
        }
    )

    expected = (
        Path.home().parent / "_docking_audit_absent_user" / "icon.png"
        if os.name == "nt"
        else None
    )
    assert custom_icon_path(config, item) == expected


@pytest.mark.skipif(os.name == "nt", reason="POSIX byte filenames")
def test_thumbnail_reader_opens_non_utf8_filename(tmp_path):
    from gi.repository import GdkPixbuf

    source = tmp_path / "source.png"
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 3, 3)
    pixbuf.fill(0xAABBCCFF)
    pixbuf.savev(str(source), "png", [], [])
    path = tmp_path / os.fsdecode(b"audit-\xff.png")
    source.rename(path)

    result = IconLoader().resolve_file_icon_info(
        target=path.as_uri(), gicon=None, content_type="image/png", size=3, is_dir=False
    )

    assert result.is_thumbnail
    assert result.icon is not None
    assert result.icon.get_pixels() == pixbuf.get_pixels()


@pytest.mark.parametrize(
    "consume", [local_path_from_uri_or_path, local_path_from_target, file_path_from_uri]
)
@pytest.mark.parametrize("authority", ["", "localhost", "LOCALHOST", None])
def test_local_file_uri_keeps_escaped_path(tmp_path, consume, authority):
    path = tmp_path / "a b.desktop"
    path.write_text("[Desktop Entry]\nType=Application\nName=Audit\nExec=true\n")
    uri_path = path.as_uri().removeprefix("file://")
    target = (
        f"file:{uri_path}" if authority is None else f"file://{authority}{uri_path}"
    )

    assert consume(target) == path
