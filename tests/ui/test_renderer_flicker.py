"""Tests for flicker-free rendering -- offscreen surface + atomic blit.

Guards against regressions where the window surface is cleared to transparent
before drawing, causing compositor-visible flicker during fast mouse movement.
"""

import cairo

from docking.ui.renderer import DockRenderer


class TestOffscreenRendering:
    """draw() must use offscreen surface and OPERATOR_SOURCE blit."""

    def test_draw_content_exists(self):
        # The flicker fix relies on draw() calling _draw_content() on an
        # offscreen surface, then blitting to the window. Verify the method exists.
        renderer = DockRenderer()
        assert hasattr(renderer, "_draw_content")
        assert callable(renderer._draw_content)

    def test_draw_method_creates_offscreen(self):
        # Verify draw() source code uses create_similar + OPERATOR_SOURCE
        # (structural test -- ensures the pattern isn't accidentally removed)
        import inspect

        source = inspect.getsource(DockRenderer.draw)
        assert "create_similar" in source, "draw() must create offscreen surface"
        assert "OPERATOR_SOURCE" in source, "draw() must blit with SOURCE operator"
        assert "_draw_content" in source, "draw() must delegate to _draw_content"

    def test_draw_method_does_not_clear(self):
        # Verify draw() does NOT use OPERATOR_CLEAR on the window context
        import inspect

        source = inspect.getsource(DockRenderer.draw)
        assert (
            "OPERATOR_CLEAR" not in source
        ), "draw() must not CLEAR the window surface -- use offscreen + SOURCE blit"

    def test_draw_content_does_not_clear_either(self):
        # _draw_content renders to a fresh offscreen surface (starts transparent),
        # so it should not need OPERATOR_CLEAR either
        import inspect

        source = inspect.getsource(DockRenderer._draw_content)
        assert "OPERATOR_CLEAR" not in source
