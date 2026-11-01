"""Gio's Windows boundary retains drive and UNC filesystem identities."""

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from docking.core.paths import local_path_from_uri_or_path
from docking.platform.files import file_for_uri
from tests.platform_factories import make_icon_loader


def test_gio_file_uri_resolves_the_actual_native_file(tmp_path):
    path = tmp_path / "report # résumé 100%.txt"
    path.write_text("native filesystem content", encoding="utf-8")
    gfile = file_for_uri(path.as_uri())
    assert Path(gfile.get_path()) == path
    assert gfile.query_exists(None)
    assert local_path_from_uri_or_path(gfile.get_uri()) == path


@pytest.mark.skipif(os.name != "nt", reason="Gio Win32 UNC conversion")
def test_gio_file_keeps_unc_server_instead_of_opening_a_local_path():
    gfile = file_for_uri("file://server/share/a%20b.txt")
    expected = Path(r"\\server\share\a b.txt")
    assert Path(gfile.get_path()) == expected
    assert local_path_from_uri_or_path(gfile.get_uri()) == expected


@pytest.mark.skipif(os.name != "nt", reason="Gio Win32 UNC conversion")
@pytest.mark.parametrize(
    "uri", ["file:relative.txt", "file://user@server/share/a", "file:///C:/a%00b"]
)
def test_invalid_windows_file_uri_is_not_passed_to_gios_uri_parser(uri):
    with pytest.raises(ValueError):
        file_for_uri(uri)


def test_native_file_metadata_keeps_image_thumbnails(tmp_path):
    from gi.repository import GdkPixbuf

    from docking.platform.targets import TargetService

    path = tmp_path / "Colors # résumé 100%.png"
    image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 4, 4)
    image.fill(0x28AA50FF)
    image.savev(str(path), "png", [], [])
    info = TargetService(
        application_registry=MagicMock(), icon_loader=make_icon_loader()
    ).resolve_file(path.as_uri(), 4)
    assert info is not None and info.is_thumbnail and info.icon is not None
    assert info.icon.get_pixels() == image.get_pixels()
