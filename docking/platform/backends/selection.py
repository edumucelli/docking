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

"""Session backend selection.

This module keeps display-server decisions in one place so X11, reduced, and
native Wayland paths can stay lazy and isolated from each other.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from docking.log import get_logger
from docking.platform.environment import (
    Desktop,
    backend_name,
    detect_desktop,
    is_wayland_session,
    is_x11_backend,
)

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.backends.base import SessionBackend
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel

log = get_logger(name="backend_selection")


def create_session_backend(
    *, config: Config, launcher: Launcher, model: DockModel
) -> SessionBackend:
    """Create the production session backend for the current runtime.

    X11 remains the default on X11 displays. Native Wayland first tries the
    optional layer-shell backend and falls back to reduced mode when unavailable.
    """
    requested = os.environ.get("DOCKING_BACKEND", "").strip().lower()
    if requested == "reduced":
        return _create_reduced_backend(reason="requested by DOCKING_BACKEND=reduced")
    if requested == "x11":
        return _create_x11_backend(
            config=config,
            launcher=launcher,
            model=model,
            reason="requested by DOCKING_BACKEND=x11",
        )
    if requested in {"gnome", "gnome-shell", "gnome-shell-bridge"}:
        backend = _create_gnome_shell_bridge_backend(
            launcher=launcher,
            model=model,
            reason=f"requested by DOCKING_BACKEND={requested}",
        )
        if backend is not None:
            return backend
        return _create_reduced_backend(
            reason=f"GNOME Shell bridge unavailable after DOCKING_BACKEND={requested}"
        )
    if requested in {"wayland", "wayland-layer-shell", "layer-shell"}:
        backend = _create_wayland_layer_shell_backend(
            launcher=launcher,
            model=model,
            reason=f"requested by DOCKING_BACKEND={requested}",
        )
        if backend is not None:
            return backend
        return _create_reduced_backend(
            reason=f"layer-shell unavailable after DOCKING_BACKEND={requested}"
        )
    if requested in {"cosmic", "cosmic-session"}:
        backend = _create_cosmic_backend(
            launcher=launcher,
            model=model,
            reason=f"requested by DOCKING_BACKEND={requested}",
        )
        if backend is not None:
            return backend
        return _create_reduced_backend(
            reason=f"COSMIC backend unavailable after DOCKING_BACKEND={requested}"
        )

    if not is_x11_backend():
        # COSMIC takes priority on its native desktop
        if detect_desktop() is Desktop.COSMIC:
            backend = _create_cosmic_backend(
                launcher=launcher,
                model=model,
                reason=_non_x11_reason(),
            )
            if backend is not None:
                return backend
        backend = _create_wayland_layer_shell_backend(
            launcher=launcher,
            model=model,
            reason=_non_x11_reason(),
        )
        if backend is not None:
            return backend
        backend = _create_gnome_shell_bridge_backend(
            launcher=launcher,
            model=model,
            reason=_non_x11_reason(),
        )
        if backend is not None:
            return backend
        return _create_reduced_backend(reason=_non_x11_reason())

    return _create_x11_backend(
        config=config,
        launcher=launcher,
        model=model,
        reason="GTK display is X11",
    )


def _create_reduced_backend(*, reason: str) -> SessionBackend:
    from docking.platform.backends.reduced.session import ReducedSessionBackend

    backend = ReducedSessionBackend()
    log.info("Selected session backend: %s (%s)", backend.name, reason)
    return backend


def _create_wayland_layer_shell_backend(
    *, launcher: Launcher, model: DockModel, reason: str
) -> SessionBackend | None:
    from docking.platform.backends.wayland.services import (
        layer_shell_is_supported,
        load_gtk_layer_shell,
    )
    from docking.platform.backends.wayland.session import (
        WaylandLayerShellSessionBackend,
    )

    layer_shell = load_gtk_layer_shell()
    if layer_shell is None:
        log.info("Wayland layer-shell backend unavailable: GtkLayerShell not installed")
        return None
    if not layer_shell_is_supported(layer_shell):
        log.info(
            "Wayland layer-shell backend unavailable: compositor does not support "
            "layer-shell"
        )
        return None
    backend = WaylandLayerShellSessionBackend(
        layer_shell=layer_shell,
        launcher=launcher,
        model=model,
    )
    log.info("Selected session backend: %s (%s)", backend.name, reason)
    return backend


def _create_gnome_shell_bridge_backend(
    *, launcher: Launcher, model: DockModel, reason: str
) -> SessionBackend | None:
    from docking.platform.backends.gnome.bridge import GnomeShellBridgeClient
    from docking.platform.backends.gnome.session import GnomeShellBridgeSessionBackend

    bridge = GnomeShellBridgeClient.connect()
    if bridge is None:
        return None
    backend = GnomeShellBridgeSessionBackend(
        model=model,
        launcher=launcher,
        bridge=bridge,
    )
    log.info("Selected session backend: %s (%s)", backend.name, reason)
    return backend


def _create_x11_backend(
    *, config: Config, launcher: Launcher, model: DockModel, reason: str
) -> SessionBackend:
    from docking.platform.backends.x11.session import X11SessionBackend

    backend = X11SessionBackend(
        model=model,
        launcher=launcher,
        config=config,
    )
    log.info("Selected session backend: %s (%s)", backend.name, reason)
    return backend


def _create_cosmic_backend(
    *, launcher: Launcher, model: DockModel, reason: str
) -> SessionBackend | None:
    from docking.platform.backends.wayland.cosmic_session import CosmicSessionBackend
    from docking.platform.backends.wayland.services import (
        layer_shell_is_supported,
        load_gtk_layer_shell,
    )

    layer_shell = load_gtk_layer_shell()
    if layer_shell is None:
        log.info("COSMIC backend unavailable: GtkLayerShell not installed")
        return None
    if not layer_shell_is_supported(layer_shell):
        log.info("COSMIC backend unavailable: compositor does not support layer-shell")
        return None
    backend = CosmicSessionBackend(
        layer_shell=layer_shell,
        launcher=launcher,
        model=model,
    )
    log.info("Selected session backend: %s (%s)", backend.name, reason)
    return backend


def _non_x11_reason() -> str:
    session = "native Wayland" if is_wayland_session() else "non-X11"
    return f"{session} GTK backend: {backend_name()}"
