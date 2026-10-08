"""Focused tests for the platform icon-loading service."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

try:
    import gi  # noqa: F401
except Exception:
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.platform import icons as icons_mod
from docking.platform.applications.types import (
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
)
from docking.platform.icons import FileIconInfo, IconLoader


@pytest.mark.parametrize("image_format", ["png", "jpeg"])
def test_file_thumbnail_provenance_survives_cache_and_pixbuf_wrapper(
    tmp_path, image_format
):
    path = tmp_path / f"photo.{image_format}"
    pixbuf = icons_mod.GdkPixbuf.Pixbuf.new(
        icons_mod.GdkPixbuf.Colorspace.RGB, False, 8, 80, 40
    )
    pixbuf.fill(0x4684DCFF)
    pixbuf.savev(str(path), image_format, [], [])
    loader = IconLoader()
    args = {
        "target": path.as_uri(),
        "gicon": None,
        "content_type": f"image/{image_format}",
        "size": 48,
        "is_dir": False,
    }

    result = loader.resolve_file_icon_info(**args)

    assert result.is_thumbnail is True
    assert (result.icon.get_width(), result.icon.get_height()) == (48, 24)
    assert loader.resolve_file_icon_info(**args) == result
    assert loader.resolve_file_icon(**args) is result.icon


@pytest.mark.parametrize("failure", ["missing", "decode", "disappeared", "empty"])
def test_failed_image_thumbnail_keeps_fallback_icon_unstyled(
    tmp_path, monkeypatch, failure
):
    path = tmp_path / "broken.png"
    if failure != "missing":
        path.write_bytes(b"not an image")
    loader = IconLoader()
    fallback = object()
    monkeypatch.setattr(loader, "load_gicon", lambda **_kwargs: fallback)
    if failure == "disappeared":
        monkeypatch.setattr(
            loader,
            "_load_cached_file_icon",
            MagicMock(side_effect=FileNotFoundError("removed before decode")),
        )
    elif failure == "empty":
        monkeypatch.setattr(loader, "_load_cached_file_icon", lambda **_kwargs: None)

    result = loader.resolve_file_icon_info(
        target=path.as_uri(),
        gicon=None,
        content_type="image/png",
        size=48,
        is_dir=False,
    )

    assert result == FileIconInfo(icon=fallback, is_thumbnail=False)


def _application(**changes) -> ApplicationInfo:
    values = {
        "desktop_id": "org.example.App.desktop",
        "name": "Example",
        "declared_icon": "org.example.App",
        "wm_class": "Example",
        "exec_line": "/opt/example/bin/example %U",
        "origin": ApplicationOrigin.INSTALLED,
        "location": ApplicationLocation.SANDBOX,
        "desktop_file": None,
        "executable_path": Path("/opt/example/bin/example"),
        "aliases": ("example",),
        "visible": True,
        "has_gio_source": True,
    }
    values.update(changes)
    return ApplicationInfo(**values)


def test_default_file_normalizer_handles_only_local_targets(tmp_path):
    path = tmp_path / "photo one.png"

    assert (
        icons_mod._normalize_file_target_for_icon(str(path)) == path.resolve().as_uri()
    )
    assert (
        icons_mod._normalize_file_target_for_icon(path.resolve().as_uri())
        == path.resolve().as_uri()
    )
    assert (
        icons_mod._normalize_file_target_for_icon("https://example.com/photo.png")
        is None
    )
    assert icons_mod._normalize_file_target_for_icon("") is None


def test_named_icon_cache_preserves_pixbuf_identity(monkeypatch):
    loader = IconLoader()
    pixbuf = object()
    load = MagicMock(return_value=pixbuf)
    monkeypatch.setattr(loader, "_try_load_icon", load)

    first = loader.load_icon("example", 48)
    second = loader.load_icon("example", 48)

    assert first is pixbuf
    assert second is first
    load.assert_called_once_with(icon_name="example", size=48)


def test_host_icon_candidates_and_theme_search_paths(tmp_path, monkeypatch):
    pixmaps = tmp_path / "run" / "host" / "usr" / "share" / "pixmaps"
    pixmaps.mkdir(parents=True)
    existing = tmp_path / "existing"
    existing.mkdir()
    theme = MagicMock()
    theme.get_search_path.return_value = [str(existing)]
    monkeypatch.setattr(
        icons_mod.Gtk.IconTheme,
        "get_default",
        lambda: theme,
        raising=False,
    )
    monkeypatch.setattr(
        icons_mod,
        "HOST_PIXMAP_DIRS",
        (str(existing), str(pixmaps), str(tmp_path / "missing")),
    )

    assert icons_mod._host_icon_file_candidates("/opt/example/icon.png") == [
        Path("/opt/example/icon.png"),
        Path("/run/host/opt/example/icon.png"),
    ]
    assert icons_mod._host_icon_file_candidates("/run/host/opt/example/icon.png") == [
        Path("/run/host/opt/example/icon.png")
    ]
    assert icons_mod._host_icon_file_candidates("example") == []
    assert icons_mod._create_icon_theme() is theme
    theme.append_search_path.assert_called_once_with(str(pixmaps))


def test_gicon_lookup_is_cached_by_string_identity(monkeypatch):
    loader = IconLoader()
    pixbuf = object()
    icon_info = MagicMock()
    icon_info.load_icon.return_value = pixbuf
    theme = MagicMock()
    theme.lookup_by_gicon.return_value = icon_info
    monkeypatch.setattr(icons_mod, "_create_icon_theme", lambda: theme)
    gicon = MagicMock()
    gicon.to_string.return_value = "folder"

    first = loader.load_gicon(gicon, 48)
    second = loader.load_gicon(gicon, 48)

    assert first is pixbuf
    assert second is first
    theme.lookup_by_gicon.assert_called_once_with(
        gicon,
        48,
        icons_mod.Gtk.IconLookupFlags.FORCE_SIZE,
    )


def test_desktop_icon_tries_executable_basename_before_generic_fallback(
    monkeypatch,
):
    loader = IconLoader()
    pixbuf = object()
    lookup = MagicMock(side_effect=[None, pixbuf])
    fallback = MagicMock()
    monkeypatch.setattr(loader, "_try_load_icon_without_fallback", lookup)
    monkeypatch.setattr(loader, "_try_load_fallback_icon", fallback)
    info = _application(
        desktop_id="org.example.Archive.desktop",
        name="Archive",
        declared_icon="missing-archive",
        wm_class="Archive",
        exec_line="/usr/bin/file-roller %U",
    )

    assert loader.load_desktop_icon(info, 48) is pixbuf
    assert lookup.call_args_list == [
        call(icon_name="missing-archive", size=48),
        call(icon_name="file-roller", size=48),
    ]
    fallback.assert_not_called()


def test_missing_icon_uses_and_caches_fallback_or_absence(monkeypatch):
    fallback_pixbuf = object()
    loader = IconLoader()
    lookup = MagicMock(return_value=None)
    fallback = MagicMock(return_value=fallback_pixbuf)
    monkeypatch.setattr(loader, "_try_load_icon_without_fallback", lookup)
    monkeypatch.setattr(loader, "_try_load_fallback_icon", fallback)

    assert loader.load_icon("missing", 48) is fallback_pixbuf
    assert loader.load_icon("missing", 48) is fallback_pixbuf
    lookup.assert_called_once()
    fallback.assert_called_once()

    absent_loader = IconLoader()
    absent_lookup = MagicMock(return_value=None)
    absent_fallback = MagicMock(return_value=None)
    monkeypatch.setattr(
        absent_loader,
        "_try_load_icon_without_fallback",
        absent_lookup,
    )
    monkeypatch.setattr(absent_loader, "_try_load_fallback_icon", absent_fallback)

    assert absent_loader.load_icon("also-missing", 48) is None
    assert absent_loader.load_icon("also-missing", 48) is None
    absent_lookup.assert_called_once()
    absent_fallback.assert_called_once()


def test_file_thumbnail_cache_is_lru(tmp_path, monkeypatch):
    paths = [tmp_path / name for name in ("a.png", "b.png", "c.png")]
    for path in paths:
        path.write_bytes(b"image")
    pixbufs = [object() for _ in range(4)]
    pixbuf_cls = MagicMock()
    pixbuf_cls.new_from_file_at_scale.side_effect = pixbufs
    monkeypatch.setattr(
        icons_mod.GdkPixbuf,
        "Pixbuf",
        pixbuf_cls,
        raising=False,
    )
    monkeypatch.setattr(icons_mod, "FILE_ICON_CACHE_MAX_ENTRIES", 2)
    loader = IconLoader()

    first = loader.load_icon_file(paths[0], 48)
    second = loader.load_icon_file(paths[1], 48)
    assert loader.load_icon_file(paths[0], 48) is first
    third = loader.load_icon_file(paths[2], 48)

    cached_paths = {key[0] for key in loader._file_icon_cache}
    assert cached_paths == {str(paths[0]), str(paths[2])}
    assert loader.load_icon_file(paths[1], 48) is pixbufs[3]
    assert (first, second, third) == tuple(pixbufs[:3])
    assert pixbuf_cls.new_from_file_at_scale.call_count == 4


def test_application_metadata_reuses_desktop_cache_identity(monkeypatch):
    loader = IconLoader()
    pixbuf = object()
    lookup = MagicMock(return_value=pixbuf)
    monkeypatch.setattr(loader, "_try_load_icon_without_fallback", lookup)
    canonical = _application()
    first = loader.load_desktop_icon(canonical, 48)
    second = loader.load_desktop_icon(canonical, 48)

    assert first is pixbuf
    assert second is first
    lookup.assert_called_once_with(icon_name=canonical.declared_icon, size=48)
