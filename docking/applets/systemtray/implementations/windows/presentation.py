"""Windows has no XEmbed legacy tray presentation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from docking.applets.systemtray.presentation import TrayPresentation
from docking.i18n import _

if TYPE_CHECKING:
    from gi.repository import Gtk


class WindowsTrayPresentation(TrayPresentation):
    @property
    def active(self) -> bool:
        return False

    @property
    def count(self) -> int:
        return 0

    def tooltip(self) -> str:
        return _("System Tray: no tray apps")

    def clicked(self) -> bool:
        return False

    def menu_items(self) -> list[Gtk.MenuItem]:
        return []

    def position(self) -> None:
        # Native provider geometry belongs to TrayService, not a legacy window.
        pass

    def empty_message(self) -> str:
        return _("System Tray: no tray apps")

    def stop(self) -> None:
        # This unavailable extension owns no native resources.
        pass
