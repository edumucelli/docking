"""Main-thread application artwork shared by menus, Search and window previews."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

from docking.platform.applications.listing import listing_gicon
from docking.platform.applications.types import ApplicationInfo, ApplicationListing

if TYPE_CHECKING:
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.icons import IconLoader
    from docking.platform.model import DockModel


class ApplicationIcons:
    """Borrow current dock artwork or resolve canonical and ID-less listings."""

    def __init__(
        self, *, model: DockModel, registry: ApplicationRegistry, loader: IconLoader
    ) -> None:
        self._model = model
        self._registry = registry
        self._loader = loader
        self._scaled: OrderedDict[tuple[GdkPixbuf.Pixbuf, int], GdkPixbuf.Pixbuf] = (
            OrderedDict()
        )

    def _model_icon(self, desktop_id: str, size: int) -> GdkPixbuf.Pixbuf | None:
        item = self._model.find_by_desktop_id(desktop_id)
        if item is None or not isinstance(item.icon, GdkPixbuf.Pixbuf):
            return None
        image = item.icon
        factor = min(size / image.get_width(), size / image.get_height())
        width = max(1, round(image.get_width() * factor))
        height = max(1, round(image.get_height() * factor))
        if (width, height) == (image.get_width(), image.get_height()):
            return image
        key = (image, size)
        if key not in self._scaled:
            scaled = image.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
            if scaled is None:
                return None
            self._scaled[key] = scaled
            while len(self._scaled) > 64:
                self._scaled.popitem(last=False)
        self._scaled.move_to_end(key)
        return self._scaled[key]

    def load(self, desktop_id: str, size: int) -> GdkPixbuf.Pixbuf | None:
        if size <= 0:
            return None
        current = self._model_icon(desktop_id, size)
        if current is not None:
            return current
        application = self._registry.get(desktop_id)
        return (
            self._loader.load_desktop_icon(info=application, size=size)
            if application is not None
            else None
        )

    def load_listing(
        self, listing: ApplicationListing, size: int
    ) -> GdkPixbuf.Pixbuf | None:
        if size <= 0:
            return None
        if isinstance(listing, ApplicationInfo):
            current = self._model_icon(listing.desktop_id, size)
            if current is not None:
                return current
            return self._loader.load_desktop_icon(
                info=listing, size=size, gicon=listing_gicon(self._registry, listing)
            )
        return self._loader.load_gicon(
            gicon=listing_gicon(self._registry, listing), size=size
        )

    def subscribe(self, changed: Callable[[str | None], None]) -> Callable[[], None]:
        self._scaled.clear()
        unsubscribe_native = self._loader.subscribe(changed)

        def model_changed() -> None:
            self._scaled.clear()
            changed(None)

        def applet_changed(desktop_id: str) -> None:
            self._scaled.clear()
            changed(desktop_id)

        self._model.add_change_listener(model_changed)
        self._model.add_applet_change_listener(applet_changed)
        subscribed = True

        def unsubscribe() -> None:
            nonlocal subscribed
            if subscribed:
                subscribed = False
                unsubscribe_native()
                self._model.remove_change_listener(model_changed)
                self._model.remove_applet_change_listener(applet_changed)

        return unsubscribe
