# pywayland protocol binding for zcosmic_workspace_manager_v2
# ruff: noqa

# Adapted from cosmic-workspace-unstable-v1.xml
# The compositor advertises zcosmic_workspace_manager_v2
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

from .zcosmic_workspace_group_handle_v1 import ZcosmicWorkspaceGroupHandleV1


class ZcosmicWorkspaceManagerV2(Interface):
    """List and control workspaces.

    After a client binds the zcosmic_workspace_manager_v2, each workspace
    will be sent via the workspace event.
    """

    name = "zcosmic_workspace_manager_v2"
    version = 2


class ZcosmicWorkspaceManagerV2Proxy(Proxy[ZcosmicWorkspaceManagerV2]):
    interface = ZcosmicWorkspaceManagerV2

    @ZcosmicWorkspaceManagerV2.request()
    def commit(self) -> None:
        """All requests about the workspaces have been sent."""
        self._marshal(0)

    @ZcosmicWorkspaceManagerV2.request()
    def stop(self) -> None:
        """Stop sending events."""
        self._marshal(1)


class ZcosmicWorkspaceManagerV2Resource(Resource):
    interface = ZcosmicWorkspaceManagerV2

    @ZcosmicWorkspaceManagerV2.event(
        Argument(ArgumentType.NewId, interface=ZcosmicWorkspaceGroupHandleV1),
    )
    def workspace_group(self, group: ZcosmicWorkspaceGroupHandleV1) -> None:
        """A workspace group has been created."""
        self._post_event(0, group)

    @ZcosmicWorkspaceManagerV2.event()
    def done(self) -> None:
        """All information about the workspace groups has been sent."""
        self._post_event(1)

    @ZcosmicWorkspaceManagerV2.event()
    def finished(self) -> None:
        """The compositor has finished with the workspace_manager."""
        self._post_event(2)


class ZcosmicWorkspaceManagerV2Global(Global):
    interface = ZcosmicWorkspaceManagerV2


ZcosmicWorkspaceManagerV2._gen_c()
ZcosmicWorkspaceManagerV2.proxy_class = ZcosmicWorkspaceManagerV2Proxy
ZcosmicWorkspaceManagerV2.resource_class = ZcosmicWorkspaceManagerV2Resource
ZcosmicWorkspaceManagerV2.global_class = ZcosmicWorkspaceManagerV2Global
