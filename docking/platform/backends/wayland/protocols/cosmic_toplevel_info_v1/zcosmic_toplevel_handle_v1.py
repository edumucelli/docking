# pywayland protocol binding for zcosmic_toplevel_handle_v1
# ruff: noqa

# Generated from cosmic-toplevel-info-unstable-v1.xml
# Copyright © 2018 Ilia Bozhinov
# Copyright © 2020 Isaac Freund
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


class ZcosmicToplevelHandleV1(Interface):
    """An open toplevel window.

    A zcosmic_toplevel_handle_v1 object represents an open toplevel
    window. A single app may have multiple open toplevels.

    Each toplevel has a list of outputs it is visible on, exposed to the
    client via the output_enter and output_leave events.
    """

    name = "zcosmic_toplevel_handle_v1"
    version = 3


class ZcosmicToplevelHandleV1Proxy(Proxy[ZcosmicToplevelHandleV1]):
    interface = ZcosmicToplevelHandleV1

    @ZcosmicToplevelHandleV1.request()
    def destroy(self) -> None:
        """Destroy the zcosmic_toplevel_handle_v1 object."""
        self._marshal(0)
        self._destroy()


class ZcosmicToplevelHandleV1Resource(Resource):
    interface = ZcosmicToplevelHandleV1

    @ZcosmicToplevelHandleV1.event()
    def closed(self) -> None:
        """The toplevel has been closed (deprecated since v2)."""
        self._post_event(0)

    @ZcosmicToplevelHandleV1.event()
    def done(self) -> None:
        """All information about the toplevel has been sent (deprecated since v2)."""
        self._post_event(1)

    @ZcosmicToplevelHandleV1.event(
        Argument(ArgumentType.String),
    )
    def title(self, title: str) -> None:
        """Title change (deprecated since v2)."""
        self._post_event(2, title)

    @ZcosmicToplevelHandleV1.event(
        Argument(ArgumentType.String),
    )
    def app_id(self, app_id: str) -> None:
        """App ID change (deprecated since v2)."""
        self._post_event(3, app_id)

    @ZcosmicToplevelHandleV1.event(
        Argument(ArgumentType.Object),
    )
    def output_enter(self, output: object) -> None:
        """Toplevel entered an output."""
        self._post_event(4, output)

    @ZcosmicToplevelHandleV1.event(
        Argument(ArgumentType.Object),
    )
    def output_leave(self, output: object) -> None:
        """Toplevel left an output."""
        self._post_event(5, output)

    @ZcosmicToplevelHandleV1.event(
        Argument(ArgumentType.Object),
    )
    def workspace_enter(self, workspace: object) -> None:
        """Toplevel entered a workspace (deprecated since v3)."""
        self._post_event(6, workspace)

    @ZcosmicToplevelHandleV1.event(
        Argument(ArgumentType.Object),
    )
    def workspace_leave(self, workspace: object) -> None:
        """Toplevel left a workspace (deprecated since v3)."""
        self._post_event(7, workspace)

    @ZcosmicToplevelHandleV1.event(
        Argument(ArgumentType.Array),
    )
    def state(self, state: object) -> None:
        """The toplevel state changed.

        Emitted once on creation and again whenever the state changes.
        State is an array of 32-bit unsigned integers.
        """
        self._post_event(8, state)

    @ZcosmicToplevelHandleV1.event(
        Argument(ArgumentType.Object),
        Argument(ArgumentType.Int),
        Argument(ArgumentType.Int),
        Argument(ArgumentType.Int),
        Argument(ArgumentType.Int),
        version=2,
    )
    def geometry(self, output: object, x: int, y: int, width: int, height: int) -> None:
        """The toplevel's geometry relative to an output has changed."""
        self._post_event(9, output, x, y, width, height)

    @ZcosmicToplevelHandleV1.event(
        Argument(ArgumentType.Object),
        version=3,
    )
    def ext_workspace_enter(self, workspace: object) -> None:
        """Toplevel entered an ext_workspace (since v3)."""
        self._post_event(10, workspace)

    @ZcosmicToplevelHandleV1.event(
        Argument(ArgumentType.Object),
        version=3,
    )
    def ext_workspace_leave(self, workspace: object) -> None:
        """Toplevel left an ext_workspace (since v3)."""
        self._post_event(11, workspace)


class ZcosmicToplevelHandleV1Global(Global):
    interface = ZcosmicToplevelHandleV1


ZcosmicToplevelHandleV1._gen_c()
ZcosmicToplevelHandleV1.proxy_class = ZcosmicToplevelHandleV1Proxy
ZcosmicToplevelHandleV1.resource_class = ZcosmicToplevelHandleV1Resource
ZcosmicToplevelHandleV1.global_class = ZcosmicToplevelHandleV1Global
