"""X11 backend service adapters.

Modules in this package implement the backend-neutral service contracts from
``docking.platform.backends.base``. Low-level Wnck, GdkX11, Xlib, XFixes, and
XScreenSaver mechanics belong in ``docking.platform.backends.x11.impl``.
"""

from docking.platform.backends.x11.services.actions import WnckDesktopActionService
from docking.platform.backends.x11.services.capture import X11ScreenCaptureService
from docking.platform.backends.x11.services.idle import X11IdleService
from docking.platform.backends.x11.services.picking import WnckWindowPickService
from docking.platform.backends.x11.services.previews import X11PreviewService
from docking.platform.backends.x11.services.surface import X11SurfaceService
from docking.platform.backends.x11.services.visibility import X11VisibilityService
from docking.platform.backends.x11.services.windows import X11WindowService
from docking.platform.backends.x11.services.workspaces import WnckWorkspaceService

__all__ = [
    "WnckDesktopActionService",
    "WnckWindowPickService",
    "WnckWorkspaceService",
    "X11IdleService",
    "X11PreviewService",
    "X11ScreenCaptureService",
    "X11SurfaceService",
    "X11VisibilityService",
    "X11WindowService",
]
