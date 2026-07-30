"""Tests for Search image thumbnail caching."""

from __future__ import annotations

import os

from gi.repository import GdkPixbuf

from docking.ui.search_thumbnails import SearchImageCache


def _write_png(path, *, width: int, height: int) -> None:
    pixbuf = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB,
        False,
        8,
        width,
        height,
    )
    pixbuf.fill(0x3366CCFF)
    pixbuf.savev(str(path), "png", [], [])


def test_thumbnail_preserves_aspect_ratio_and_is_cached(tmp_path) -> None:
    path = tmp_path / "wide.png"
    _write_png(path, width=120, height=60)
    cache = SearchImageCache()

    first = cache.load(path=str(path), max_width=32, max_height=32)
    second = cache.load(path=str(path), max_width=32, max_height=32)

    assert first is not None
    assert first.pixbuf.get_width() == 32
    assert first.pixbuf.get_height() == 16
    assert second is first


def test_file_modification_invalidates_thumbnail(tmp_path) -> None:
    path = tmp_path / "changing.png"
    _write_png(path, width=120, height=60)
    cache = SearchImageCache()
    first = cache.load(path=str(path), max_width=32, max_height=32)
    previous_mtime = path.stat().st_mtime_ns

    _write_png(path, width=60, height=120)
    os.utime(path, ns=(previous_mtime + 1_000_000, previous_mtime + 1_000_000))
    second = cache.load(path=str(path), max_width=32, max_height=32)

    assert first is not None
    assert second is not None
    assert second is not first
    assert second.pixbuf.get_width() == 16
    assert second.pixbuf.get_height() == 32


def test_invalid_image_returns_none(tmp_path) -> None:
    path = tmp_path / "broken.png"
    path.write_text("not an image")

    assert (
        SearchImageCache().load(
            path=str(path),
            max_width=32,
            max_height=32,
        )
        is None
    )


def test_excessive_pixel_count_is_rejected_before_decode(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "bomb.png"
    path.write_bytes(b"small compressed payload")
    monkeypatch.setattr(
        GdkPixbuf.Pixbuf,
        "get_file_info",
        lambda _path: (object(), 10_000, 10_000),
    )

    def fail_decode(*_args):
        raise AssertionError("decoded")

    monkeypatch.setattr(
        GdkPixbuf.Pixbuf,
        "new_from_file_at_scale",
        fail_decode,
    )

    loaded = SearchImageCache().load(
        path=str(path),
        max_width=32,
        max_height=32,
    )

    assert loaded is None
