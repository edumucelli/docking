"""Reader tile semantics and font-independent color regression checks."""

from unittest.mock import MagicMock

import pytest

import docking.applets.reader_tile as reader_tile
from docking.applets.news.render import render_icon as render_news
from docking.applets.reddit.render import render_icon as render_reddit


@pytest.mark.parametrize(
    "render,mark,normal_pixels,loading_pixels",
    [
        (
            render_news,
            "N",
            ((48, 113, 181, 249), (25, 97, 173, 248), (16, 51, 87, 252)),
            ((75, 139, 193, 200), (50, 122, 184, 194), (26, 55, 82, 226)),
        ),
        (
            render_reddit,
            "r/",
            ((238, 83, 43, 249), (236, 64, 20, 248), (116, 35, 15, 252)),
            ((228, 101, 51, 200), (225, 79, 22, 194), (98, 38, 16, 226)),
        ),
    ],
)
@pytest.mark.parametrize("size", [32, 47, 128])
@pytest.mark.parametrize(
    "count,index,empty_or_rank",
    [(0, 99, "RSS"), (1, -5, "1"), (10, 2, "3"), (10, 99, "100"), (-1, 2, "3")],
)
@pytest.mark.parametrize("loading", [False, True])
@pytest.mark.parametrize("error", [False, True])
def test_reader_tile_states_and_pixels(
    monkeypatch,
    render,
    mark,
    normal_pixels,
    loading_pixels,
    size,
    count,
    index,
    empty_or_rank,
    loading,
    error,
):
    text = MagicMock()
    badge = MagicMock(wraps=reader_tile.draw_warning_badge)
    monkeypatch.setattr(reader_tile, "_draw_centered_text", text)
    monkeypatch.setattr(reader_tile, "draw_warning_badge", badge)

    pixbuf = render(size=size, count=count, index=index, loading=loading, error=error)

    assert pixbuf is not None
    assert (pixbuf.get_width(), pixbuf.get_height()) == (size, size)
    label = "..." if not count and loading else empty_or_rank
    assert [call.kwargs["text"] for call in text.call_args_list] == [mark, label]
    assert badge.call_count == int(error)
    pixels = pixbuf.get_pixels()
    assert tuple(pixels[:4]) == (0, 0, 0, 0)
    # Sample the highlight, body and history strip without font rasterization.
    expected = loading_pixels if loading else normal_pixels
    for y, rgba in zip((0.2, 0.6, 0.8), expected, strict=True):
        offset = int(size * y) * pixbuf.get_rowstride() + (size // 2) * 4
        assert tuple(pixels[offset : offset + 4]) == rgba


@pytest.mark.parametrize("render", [render_news, render_reddit])
def test_reader_tile_rasterizes_text(render, monkeypatch):
    pixbuf = render(size=48, index=2, count=10, loading=True, error=True)
    assert pixbuf is not None
    assert (pixbuf.get_width(), pixbuf.get_height()) == (48, 48)
    monkeypatch.setattr(reader_tile, "_draw_centered_text", MagicMock())
    without_text = render(size=48, index=2, count=10, loading=True, error=True)
    assert without_text is not None
    assert pixbuf.get_pixels() != without_text.get_pixels()
