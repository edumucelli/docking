"""Resolve Search artwork from the same canonical sources as the dock.

Providers keep immutable, GTK-free result descriptors. Presentation uses their
stable desktop identities to borrow current applet/custom icons or request
application artwork through the shared loader, including its Windows workers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from gi.repository import GdkPixbuf

from docking.platform.application_icons import ApplicationIcons
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
        self._applications = ApplicationIcons(
            model=model, registry=registry, loader=loader
        )
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
            image = self._applications.load(desktop_id, size)
            if image is not None:
                return image
        return self._loader.load_icon(
            icon_name=result.icon_name or "system-search", size=size
        )

    def subscribe(self, changed: Callable[[str | None], None]) -> Callable[[], None]:
        """Observe native completion, live applets and custom/model icon changes."""
        return self._applications.subscribe(changed)
