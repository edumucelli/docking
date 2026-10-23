"""Optional dock launcher for the process-wide search palette."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import GdkPixbuf

from docking.applets.base import Applet
from docking.applets.search import meta
from docking.applets.search.render import render_icon
from docking.core.icons import IconSource
from docking.i18n import _

if TYPE_CHECKING:
    from docking.applets.services import AppletServices
    from docking.core.config import Config
    from docking.search.presenter import SearchPresenter


class SearchApplet(Applet):
    """Open Docking Search without owning its process-wide state."""

    id = meta.id
    name = _("Search")
    icon_name = "system-search"
    icon_source_options = (IconSource.DOCKING, IconSource.SYSTEM)

    def __init__(self, icon_size: int, config: Config) -> None:
        self._presenter: SearchPresenter | None = None
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_docking_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size)

    def refresh_tooltip(self) -> None:
        self.item.name = _("Search")

    def set_services(self, services: AppletServices) -> None:
        self._presenter = services.search

    def on_clicked(self) -> None:
        if self._presenter is not None:
            self._presenter.show()


__all__ = ["SearchApplet"]
