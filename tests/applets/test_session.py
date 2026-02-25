"""Tests for the session applet."""

from docking.applets.session import SessionApplet, _ACTIONS


class TestSessionApplet:
    def test_creates_with_icon(self):
        applet = SessionApplet(48)
        assert applet.item.icon is not None
        assert applet.item.name == "Session"

    def test_icon_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = SessionApplet(size)
            pixbuf = applet.create_icon(size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size

    def test_menu_has_all_actions(self):
        applet = SessionApplet(48)
        items = applet.get_menu_items()
        labels = [mi.get_label() for mi in items]
        assert labels == [label for label, _cmd in _ACTIONS]

    def test_actions_list_has_expected_entries(self):
        labels = [label for label, _cmd in _ACTIONS]
        assert "Lock Screen" in labels
        assert "Shut Down" in labels
        assert "Suspend" in labels
