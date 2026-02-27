"""Regression tests for should_keep_cursor_on_leave.

Guards against zoom-snap bug: cursor must be preserved when preview is
visible (mouse moved into preview popup) or autohide is active (smooth
zoom decay during hide animation).
"""

from docking.ui.dock_window import should_keep_cursor_on_leave


class TestKeepCursorWithPreview:
    def test_preview_visible_autohide_on(self):
        assert (
            should_keep_cursor_on_leave(autohide_enabled=True, preview_visible=True)
            is True
        )

    def test_preview_visible_autohide_off(self):
        assert (
            should_keep_cursor_on_leave(autohide_enabled=False, preview_visible=True)
            is True
        )


class TestKeepCursorWithoutPreview:
    def test_autohide_on(self):
        # Smooth zoom decay needs cursor
        assert (
            should_keep_cursor_on_leave(autohide_enabled=True, preview_visible=False)
            is True
        )

    def test_autohide_off(self):
        # Nothing to preserve for
        assert (
            should_keep_cursor_on_leave(autohide_enabled=False, preview_visible=False)
            is False
        )
