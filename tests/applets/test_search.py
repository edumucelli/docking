"""Tests for the optional Search launcher applet."""

from __future__ import annotations

from unittest.mock import MagicMock

from docking.applets import load_applet_class
from docking.applets.search.applet import SearchApplet
from docking.applets.services import AppletServices
from docking.core.config import Config


def test_search_applet_is_discoverable_and_renders() -> None:
    assert load_applet_class("search") is SearchApplet
    applet = SearchApplet(icon_size=48, config=Config())

    for size in (32, 48, 64):
        icon = applet.create_icon(size=size)
        assert icon is not None
        assert icon.get_width() == size
        assert icon.get_height() == size


def test_click_opens_process_search_presenter() -> None:
    presenter = MagicMock()
    applet = SearchApplet(icon_size=48, config=Config())
    applet.set_services(AppletServices(search=presenter))

    applet.on_clicked()

    presenter.show.assert_called_once_with()
