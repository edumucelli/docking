# pywayland protocol binding for zcosmic_toplevel_manager_v1
# ruff: noqa

# Generated from cosmic-toplevel-management-unstable-v1.xml
# Copyright © 2018 Ilia Bozhinov
# Copyright © 2020 Isaac Freund
# Copyright © 2022 wb9688
# Copyright © 2024 Victoria Brekenfeld

from __future__ import annotations

from pywayland.protocol_core import (
    Argument,
    ArgumentType,
    Global,
    Interface,
    Proxy,
    Resource,
)


class ZcosmicToplevelManagerV1(Interface):
    """Control open apps.

    This protocol allows clients such as a taskbar to request the compositor
    to perform typical actions on open toplevels. The compositor is in all
    cases free to ignore the request.
    """

    name = "zcosmic_toplevel_manager_v1"
    version = 4


class ZcosmicToplevelManagerV1Proxy(Proxy[ZcosmicToplevelManagerV1]):
    interface = ZcosmicToplevelManagerV1

    @ZcosmicToplevelManagerV1.request()
    def destroy(self) -> None:
        """Destroy the zcosmic_toplevel_manager_v1."""
        self._marshal(0)
        self._destroy()

    @ZcosmicToplevelManagerV1.request(
        Argument(ArgumentType.Object),
    )
    def close(self, toplevel: object) -> None:
        """Request that a toplevel be closed."""
        self._marshal(1, toplevel)

    @ZcosmicToplevelManagerV1.request(
        Argument(ArgumentType.Object),
        Argument(ArgumentType.Object),
    )
    def activate(self, toplevel: object, seat: object) -> None:
        """Request that a toplevel be activated."""
        self._marshal(2, toplevel, seat)

    @ZcosmicToplevelManagerV1.request(
        Argument(ArgumentType.Object),
    )
    def set_maximized(self, toplevel: object) -> None:
        """Request that a toplevel be maximized."""
        self._marshal(3, toplevel)

    @ZcosmicToplevelManagerV1.request(
        Argument(ArgumentType.Object),
    )
    def unset_maximized(self, toplevel: object) -> None:
        """Request that a toplevel be unmaximized."""
        self._marshal(4, toplevel)

    @ZcosmicToplevelManagerV1.request(
        Argument(ArgumentType.Object),
    )
    def set_minimized(self, toplevel: object) -> None:
        """Request that a toplevel be minimized."""
        self._marshal(5, toplevel)

    @ZcosmicToplevelManagerV1.request(
        Argument(ArgumentType.Object),
    )
    def unset_minimized(self, toplevel: object) -> None:
        """Request that a toplevel be unminimized."""
        self._marshal(6, toplevel)

    @ZcosmicToplevelManagerV1.request(
        Argument(ArgumentType.Object),
        Argument(ArgumentType.Object, nullable=True),
    )
    def set_fullscreen(self, toplevel: object, output: object | None) -> None:
        """Request that a toplevel be fullscreened."""
        self._marshal(7, toplevel, output)

    @ZcosmicToplevelManagerV1.request(
        Argument(ArgumentType.Object),
    )
    def unset_fullscreen(self, toplevel: object) -> None:
        """Request that a toplevel be unfullscreened."""
        self._marshal(8, toplevel)

    @ZcosmicToplevelManagerV1.request(
        Argument(ArgumentType.Object),
        Argument(ArgumentType.Object),
        Argument(ArgumentType.Int),
        Argument(ArgumentType.Int),
        Argument(ArgumentType.Int),
        Argument(ArgumentType.Int),
    )
    def set_rectangle(
        self,
        toplevel: object,
        surface: object,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        """Designate a rectangle to represent a toplevel."""
        self._marshal(9, toplevel, surface, x, y, width, height)

    @ZcosmicToplevelManagerV1.request(
        Argument(ArgumentType.Object),
        Argument(ArgumentType.Object),
        Argument(ArgumentType.Object),
        version=2,
    )
    def move_to_workspace(
        self, toplevel: object, workspace: object, output: object
    ) -> None:
        """Move toplevel to workspace (deprecated since v4)."""
        self._marshal(10, toplevel, workspace, output)

    @ZcosmicToplevelManagerV1.request(
        Argument(ArgumentType.Object),
        version=3,
    )
    def set_sticky(self, toplevel: object) -> None:
        """Request that a toplevel be made sticky."""
        self._marshal(11, toplevel)

    @ZcosmicToplevelManagerV1.request(
        Argument(ArgumentType.Object),
        version=3,
    )
    def unset_sticky(self, toplevel: object) -> None:
        """Request that a toplevel be removed of the sticky status."""
        self._marshal(12, toplevel)

    @ZcosmicToplevelManagerV1.request(
        Argument(ArgumentType.Object),
        Argument(ArgumentType.Object),
        Argument(ArgumentType.Object),
        version=4,
    )
    def move_to_ext_workspace(
        self, toplevel: object, workspace: object, output: object
    ) -> None:
        """Move toplevel to ext_workspace."""
        self._marshal(13, toplevel, workspace, output)


class ZcosmicToplevelManagerV1Resource(Resource):
    interface = ZcosmicToplevelManagerV1

    @ZcosmicToplevelManagerV1.event(
        Argument(ArgumentType.Array),
    )
    def capabilities(self, capabilities: object) -> None:
        """Compositor capabilities.

        Advertises the capabilities supported by the compositor.
        Capabilities are sent as an array of 32-bit unsigned integers.
        """
        self._post_event(0, capabilities)


class ZcosmicToplevelManagerV1Global(Global):
    interface = ZcosmicToplevelManagerV1


ZcosmicToplevelManagerV1._gen_c()
ZcosmicToplevelManagerV1.proxy_class = ZcosmicToplevelManagerV1Proxy
ZcosmicToplevelManagerV1.resource_class = ZcosmicToplevelManagerV1Resource
ZcosmicToplevelManagerV1.global_class = ZcosmicToplevelManagerV1Global
