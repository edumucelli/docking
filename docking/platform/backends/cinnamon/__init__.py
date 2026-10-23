"""Cinnamon Wayland integration.

The package stays inert at import time so its pre-GTK startup helper can run
before loading GI. Public backend classes are loaded only when requested.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from docking.platform.backends.cinnamon.muffin import (
        MuffinDebugClient as MuffinDebugClient,
    )
    from docking.platform.backends.cinnamon.muffin import (
        MuffinWindowService as MuffinWindowService,
    )
    from docking.platform.backends.cinnamon.session import (
        CinnamonWaylandSessionBackend as CinnamonWaylandSessionBackend,
    )

__all__ = [
    "CinnamonWaylandSessionBackend",
    "MuffinDebugClient",
    "MuffinWindowService",
]


def __getattr__(name: str) -> Any:
    if name in {"MuffinDebugClient", "MuffinWindowService"}:
        from docking.platform.backends.cinnamon import muffin

        return getattr(muffin, name)
    if name == "CinnamonWaylandSessionBackend":
        from docking.platform.backends.cinnamon.session import (
            CinnamonWaylandSessionBackend,
        )

        return CinnamonWaylandSessionBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
