"""Resolve Search artwork from the same canonical sources as the dock.

Providers keep immutable, GTK-free result descriptors. Presentation uses their
stable desktop identities to borrow current applet/custom icons or request
application artwork through the shared loader, including its Windows workers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from gi.repository import GdkPixbuf

from docking.search.types import SearchResult

if TYPE_CHECKING:
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.icons import IconLoader
    from docking.platform.model import DockModel


class SearchResultIcons:
    """Project current model and registry artwork without changing search policy."""

    def __init__(
        self, *, model: DockModel, registry: ApplicationRegistry, loader: IconLoader
    ) -> None:
        self._model = model
        self._registry = registry
        self._loader = loader

    def load(self, result: SearchResult, size: int) -> GdkPixbuf.Pixbuf | None:
        if size <= 0:
            return None
        desktop_id = dict(result.metadata).get("desktop_id", "")
        if desktop_id and result.identity.provider_id in {
            "applications",
            "windows",
            "dock",
        }:
            item = self._model.find_by_desktop_id(desktop_id)
            if item is not None and isinstance(item.icon, GdkPixbuf.Pixbuf):
                image = item.icon
                factor = min(size / image.get_width(), size / image.get_height())
                width = max(1, round(image.get_width() * factor))
                height = max(1, round(image.get_height() * factor))
                if (width, height) == (image.get_width(), image.get_height()):
                    return image
                return image.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
            application = self._registry.get(desktop_id)
            if application is not None:
                return self._loader.load_desktop_icon(info=application, size=size)
        return self._loader.load_icon(
            icon_name=result.icon_name or "system-search", size=size
        )

    def subscribe(self, changed: Callable[[str | None], None]) -> Callable[[], None]:
        """Observe native completion, live applets and custom/model icon changes."""
        unsubscribe_native = self._loader.subscribe(changed)

        def model_changed() -> None:
            changed(None)

        self._model.add_change_listener(model_changed)
        self._model.add_applet_change_listener(changed)
        subscribed = True

        def unsubscribe() -> None:
            nonlocal subscribed
            if subscribed:
                subscribed = False
                unsubscribe_native()
                self._model.remove_change_listener(model_changed)
                self._model.remove_applet_change_listener(changed)

        return unsubscribe
