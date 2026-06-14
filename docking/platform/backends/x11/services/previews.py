# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""X11 window preview capture service."""

from __future__ import annotations

from typing import TYPE_CHECKING

from docking.platform.backends.base import (
    DisplayServer,
    PreviewImage,
    PreviewService,
    WindowId,
)
from docking.platform.backends.x11.impl.preview_capture import (
    capture_window,
    capture_xid,
)

if TYPE_CHECKING:
    from docking.platform.backends.x11.services.windows import X11WindowService


class X11PreviewService(PreviewService):
    """PreviewService implementation backed by X11 foreign-window capture."""

    def __init__(self, window_tracker: X11WindowService) -> None:
        self._tracker = window_tracker

    def start(self) -> None:
        """No runtime loop is needed for X11 preview capture."""

    def stop(self) -> None:
        """No persistent resources are held by the preview service."""

    def capture(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        """Capture one X11 window preview by backend-neutral window ID."""
        xid = _xid_from_window_id(window_id=window_id)
        if xid is None:
            return None

        # Preserve X11 popup behavior exactly: preview thumbnails capture
        # pixels directly from the XID and do not ask Wnck whether the
        # window was minimized. Routing through capture_window() here would make
        # minimized or transiently hidden windows immediately turn into fallback
        # icons, which changes the visible popup behavior while the pointer moves
        # from the dock toward the preview.
        pixbuf = capture_xid(xid=xid, thumb_w=width, thumb_h=height)
        if pixbuf is None:
            return None
        return PreviewImage(
            image=pixbuf,
            width=int(pixbuf.get_width()),
            height=int(pixbuf.get_height()),
        )

    def thumbnail(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        """Capture an X11 menu thumbnail, including minimized fallback."""
        xid = _xid_from_window_id(window_id=window_id)
        if xid is None:
            return None
        window = self._tracker.window_for_id(window_id)
        if window is None:
            return None
        pixbuf = capture_window(wnck_window=window, thumb_w=width, thumb_h=height)
        if pixbuf is None:
            return None
        return PreviewImage(
            image=pixbuf,
            width=int(pixbuf.get_width()),
            height=int(pixbuf.get_height()),
        )


def _xid_from_window_id(*, window_id: WindowId) -> int | None:
    if window_id.backend is not DisplayServer.X11:
        return None
    try:
        return int(window_id.value)
    except (TypeError, ValueError):
        return None
