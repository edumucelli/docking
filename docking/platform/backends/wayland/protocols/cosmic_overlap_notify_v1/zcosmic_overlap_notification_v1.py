# pywayland protocol binding for zcosmic_overlap_notification_v1
# ruff: noqa

# Generated from cosmic-overlap-notify-unstable-v1.xml
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


class ZcosmicOverlapNotificationV1(Interface):
    """Subscription for overlapping toplevels on a layer-surface."""

    name = "zcosmic_overlap_notification_v1"
    version = 1


class ZcosmicOverlapNotificationV1Proxy(Proxy[ZcosmicOverlapNotificationV1]):
    interface = ZcosmicOverlapNotificationV1

    @ZcosmicOverlapNotificationV1.request()
    def destroy(self) -> None:
        """Destroy the notification object."""
        self._marshal(0)
        self._destroy()


class ZcosmicOverlapNotificationV1Resource(Resource):
    interface = ZcosmicOverlapNotificationV1

    @ZcosmicOverlapNotificationV1.event(
        Argument(ArgumentType.Object),
        Argument(ArgumentType.Int),
        Argument(ArgumentType.Int),
        Argument(ArgumentType.Int),
        Argument(ArgumentType.Int),
    )
    def toplevel_enter(
        self,
        toplevel: object,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        """A toplevel has entered the surface area."""
        self._post_event(0, toplevel, x, y, width, height)

    @ZcosmicOverlapNotificationV1.event(
        Argument(ArgumentType.Object),
    )
    def toplevel_leave(self, toplevel: object) -> None:
        """A toplevel has left the surface area."""
        self._post_event(1, toplevel)

    @ZcosmicOverlapNotificationV1.event(
        Argument(ArgumentType.String),
        Argument(ArgumentType.String),
        Argument(ArgumentType.Uint),
        Argument(ArgumentType.Uint),
        Argument(ArgumentType.Int),
        Argument(ArgumentType.Int),
        Argument(ArgumentType.Int),
        Argument(ArgumentType.Int),
    )
    def layer_enter(
        self,
        identifier: str,
        namespace: str,
        exclusive: int,
        layer: int,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        """A layer surface has entered the surface area."""
        self._post_event(
            2, identifier, namespace, exclusive, layer, x, y, width, height
        )

    @ZcosmicOverlapNotificationV1.event(
        Argument(ArgumentType.String),
    )
    def layer_leave(self, identifier: str) -> None:
        """A layer surface has left the surface area."""
        self._post_event(3, identifier)


class ZcosmicOverlapNotificationV1Global(Global):
    interface = ZcosmicOverlapNotificationV1


ZcosmicOverlapNotificationV1._gen_c()
ZcosmicOverlapNotificationV1.proxy_class = ZcosmicOverlapNotificationV1Proxy
ZcosmicOverlapNotificationV1.resource_class = ZcosmicOverlapNotificationV1Resource
ZcosmicOverlapNotificationV1.global_class = ZcosmicOverlapNotificationV1Global
