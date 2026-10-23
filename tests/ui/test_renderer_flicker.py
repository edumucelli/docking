"""Tests for flicker-free rendering -- offscreen surface + atomic blit.

Guards against regressions where the window surface is cleared to transparent
before drawing, causing compositor-visible flicker during fast mouse movement.
"""

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
        # Verify draw() requests an offscreen surface and blits with SOURCE.
        import inspect

        source = inspect.getsource(DockRenderer.draw)
        assert "offscreen_surface_for" in source, (
            "draw() must request offscreen surface"
        )
        assert "OPERATOR_SOURCE" in source, "draw() must blit with SOURCE operator"
        assert "_draw_content" in source, "draw() must delegate to _draw_content"

    def test_draw_method_does_not_clear(self):
        # The visible window surface must not be CLEARed directly; the clear is
        # allowed only on the offscreen context before the final SOURCE blit.
        import inspect

        source = inspect.getsource(DockRenderer.draw)
        assert source.count("OPERATOR_CLEAR") == 1, (
            "draw() should clear only the offscreen context once"
        )
        assert "ocr.set_operator(cairo.OPERATOR_CLEAR)" in source

    def test_draw_content_does_not_clear_either(self):
        # _draw_content only paints content; offscreen clearing belongs in draw().
        import inspect

        source = inspect.getsource(DockRenderer._draw_content)
        assert "OPERATOR_CLEAR" not in source
