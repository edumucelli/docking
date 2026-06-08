# pywayland protocol binding for zcosmic_toplevel_info_v1
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

from .zcosmic_toplevel_handle_v1 import ZcosmicToplevelHandleV1


class ZcosmicToplevelInfoV1(Interface):
    """List toplevels and properties thereof.

    The purpose of this protocol is to enable clients such as taskbars
    or docks to access a list of opened applications and basic properties
    thereof.

    It extends ext_foreign_toplevel_list_v1 to provide more information
    and actions on foreign toplevels.
    """

    name = "zcosmic_toplevel_info_v1"
    version = 3


class ZcosmicToplevelInfoV1Proxy(Proxy[ZcosmicToplevelInfoV1]):
    interface = ZcosmicToplevelInfoV1

    @ZcosmicToplevelInfoV1.request()
    def stop(self) -> None:
        """Stop sending events (deprecated since v2)."""
        self._marshal(0)

    @ZcosmicToplevelInfoV1.request(
        Argument(ArgumentType.NewId, interface=ZcosmicToplevelHandleV1),
        Argument(ArgumentType.Object, nullable=True),
        version=2,
    )
    def get_cosmic_toplevel(
        self, foreign_toplevel: object | None
    ) -> Proxy[ZcosmicToplevelHandleV1]:
        """Get a zcosmic_toplevel_handle_v1 for an ext_foreign_toplevel_handle_v1."""
        return self._marshal_constructor(1, ZcosmicToplevelHandleV1, foreign_toplevel)


class ZcosmicToplevelInfoV1Resource(Resource):
    interface = ZcosmicToplevelInfoV1

    @ZcosmicToplevelInfoV1.event(
        Argument(ArgumentType.NewId, interface=ZcosmicToplevelHandleV1),
    )
    def toplevel(self, toplevel: ZcosmicToplevelHandleV1) -> None:
        """A toplevel has been created (deprecated since v2)."""
        self._post_event(0, toplevel)

    @ZcosmicToplevelInfoV1.event()
    def finished(self) -> None:
        """The compositor has finished with the toplevel manager."""
        self._post_event(1)

    @ZcosmicToplevelInfoV1.event(version=2)
    def done(self) -> None:
        """All information about active toplevels has been sent."""
        self._post_event(2)


class ZcosmicToplevelInfoV1Global(Global):
    interface = ZcosmicToplevelInfoV1


ZcosmicToplevelInfoV1._gen_c()
ZcosmicToplevelInfoV1.proxy_class = ZcosmicToplevelInfoV1Proxy
ZcosmicToplevelInfoV1.resource_class = ZcosmicToplevelInfoV1Resource
ZcosmicToplevelInfoV1.global_class = ZcosmicToplevelInfoV1Global
