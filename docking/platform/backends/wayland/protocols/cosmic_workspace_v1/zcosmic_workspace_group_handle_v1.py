# pywayland protocol binding for zcosmic_workspace_group_handle_v1
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

from .zcosmic_workspace_handle_v1 import ZcosmicWorkspaceHandleV1


class ZcosmicWorkspaceGroupHandleV1(Interface):
    """A workspace group assigned to a set of outputs."""

    name = "zcosmic_workspace_group_handle_v1"
    version = 2


class ZcosmicWorkspaceGroupHandleV1Proxy(Proxy[ZcosmicWorkspaceGroupHandleV1]):
    interface = ZcosmicWorkspaceGroupHandleV1

    @ZcosmicWorkspaceGroupHandleV1.request()
    def destroy(self) -> None:
        """Destroy the workspace group handle."""
        self._marshal(0)
        self._destroy()

    @ZcosmicWorkspaceGroupHandleV1.request(
        Argument(ArgumentType.String),
    )
    def create_workspace(self, name: str) -> None:
        """Create a new workspace."""
        self._marshal(1, name)


class ZcosmicWorkspaceGroupHandleV1Resource(Resource):
    interface = ZcosmicWorkspaceGroupHandleV1

    @ZcosmicWorkspaceGroupHandleV1.event(
        Argument(ArgumentType.Array),
    )
    def capabilities(self, capabilities: object) -> None:
        """Compositor capabilities."""
        self._post_event(0, capabilities)

    @ZcosmicWorkspaceGroupHandleV1.event(
        Argument(ArgumentType.Object),
    )
    def output_enter(self, output: object) -> None:
        """Output assigned to workspace group."""
        self._post_event(1, output)

    @ZcosmicWorkspaceGroupHandleV1.event(
        Argument(ArgumentType.Object),
    )
    def output_leave(self, output: object) -> None:
        """Output removed from workspace group."""
        self._post_event(2, output)

    @ZcosmicWorkspaceGroupHandleV1.event(
        Argument(ArgumentType.NewId, interface=ZcosmicWorkspaceHandleV1),
    )
    def workspace(self, workspace: ZcosmicWorkspaceHandleV1) -> None:
        """Workspace added to workspace group."""
        self._post_event(3, workspace)

    @ZcosmicWorkspaceGroupHandleV1.event()
    def remove(self) -> None:
        """This workspace group has been destroyed."""
        self._post_event(4)


class ZcosmicWorkspaceGroupHandleV1Global(Global):
    interface = ZcosmicWorkspaceGroupHandleV1


ZcosmicWorkspaceGroupHandleV1._gen_c()
ZcosmicWorkspaceGroupHandleV1.proxy_class = ZcosmicWorkspaceGroupHandleV1Proxy
ZcosmicWorkspaceGroupHandleV1.resource_class = ZcosmicWorkspaceGroupHandleV1Resource
ZcosmicWorkspaceGroupHandleV1.global_class = ZcosmicWorkspaceGroupHandleV1Global
