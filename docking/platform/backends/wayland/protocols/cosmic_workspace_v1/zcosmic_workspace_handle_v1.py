# pywayland protocol binding for zcosmic_workspace_handle_v1
# ruff: noqa

# Adapted from cosmic-workspace-unstable-v1.xml
# Copyright © 2019 Christopher Billington
# Copyright © 2020 Ilia Bozhinov
# Copyright © 2022 Victoria Brekenfeld

from __future__ import annotations

from pywayland.protocol_core import (
    Argument,
    ArgumentType,
    Global,
    Interface,
    Proxy,
    Resource,
)


class ZcosmicWorkspaceHandleV1(Interface):
    """A workspace handle."""

    name = "zcosmic_workspace_handle_v1"
    version = 2


class ZcosmicWorkspaceHandleV1Proxy(Proxy[ZcosmicWorkspaceHandleV1]):
    interface = ZcosmicWorkspaceHandleV1

    @ZcosmicWorkspaceHandleV1.request()
    def destroy(self) -> None:
        """Destroy the workspace handle."""
        self._marshal(0)
        self._destroy()

    @ZcosmicWorkspaceHandleV1.request()
    def activate(self) -> None:
        """Activate this workspace."""
        self._marshal(1)

    @ZcosmicWorkspaceHandleV1.request()
    def deactivate(self) -> None:
        """Deactivate this workspace."""
        self._marshal(2)

    @ZcosmicWorkspaceHandleV1.request()
    def remove(self) -> None:
        """Remove this workspace."""
        self._marshal(3)

    @ZcosmicWorkspaceHandleV1.request(
        Argument(ArgumentType.String),
        version=2,
    )
    def rename(self, name: str) -> None:
        """Rename this workspace."""
        self._marshal(4, name)

    @ZcosmicWorkspaceHandleV1.request(
        Argument(ArgumentType.Uint),
        version=2,
    )
    def set_tiling_state(self, state: int) -> None:
        """Set tiling state."""
        self._marshal(5, state)


class ZcosmicWorkspaceHandleV1Resource(Resource):
    interface = ZcosmicWorkspaceHandleV1

    @ZcosmicWorkspaceHandleV1.event(
        Argument(ArgumentType.String),
    )
    def name(self, name: str) -> None:
        """Workspace name."""
        self._post_event(0, name)

    @ZcosmicWorkspaceHandleV1.event(
        Argument(ArgumentType.Uint),
        Argument(ArgumentType.Uint),
    )
    def coordinates(self, x: int, y: int) -> None:
        """Workspace coordinates."""
        self._post_event(1, x, y)

    @ZcosmicWorkspaceHandleV1.event(
        Argument(ArgumentType.Array),
    )
    def state(self, state: object) -> None:
        """Workspace state flags."""
        self._post_event(2, state)

    @ZcosmicWorkspaceHandleV1.event(
        Argument(ArgumentType.Array),
    )
    def capabilities(self, capabilities: object) -> None:
        """Workspace capabilities."""
        self._post_event(3, capabilities)

    @ZcosmicWorkspaceHandleV1.event()
    def remove(self) -> None:
        """This workspace has been removed."""
        self._post_event(4)

    @ZcosmicWorkspaceHandleV1.event(
        Argument(ArgumentType.Uint),
        version=2,
    )
    def tiling_state(self, state: int) -> None:
        """Tiling state."""
        self._post_event(5, state)


class ZcosmicWorkspaceHandleV1Global(Global):
    interface = ZcosmicWorkspaceHandleV1


ZcosmicWorkspaceHandleV1._gen_c()
ZcosmicWorkspaceHandleV1.proxy_class = ZcosmicWorkspaceHandleV1Proxy
ZcosmicWorkspaceHandleV1.resource_class = ZcosmicWorkspaceHandleV1Resource
ZcosmicWorkspaceHandleV1.global_class = ZcosmicWorkspaceHandleV1Global
