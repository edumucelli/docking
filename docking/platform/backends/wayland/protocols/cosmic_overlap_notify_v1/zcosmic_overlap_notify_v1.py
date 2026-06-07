# pywayland protocol binding for zcosmic_overlap_notify_v1
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

from .zcosmic_overlap_notification_v1 import ZcosmicOverlapNotificationV1


class ZcosmicOverlapNotifyV1(Interface):
    """Get notifications of other elements overlapping layer surfaces.

    The purpose of this protocol is to enable layer-shell clients to get
    notifications if part of their surfaces are occluded by other elements
    (toplevels and other layer-surfaces).
    """

    name = "zcosmic_overlap_notify_v1"
    version = 1


class ZcosmicOverlapNotifyV1Proxy(Proxy[ZcosmicOverlapNotifyV1]):
    interface = ZcosmicOverlapNotifyV1

    @ZcosmicOverlapNotifyV1.request(
        Argument(ArgumentType.NewId, interface=ZcosmicOverlapNotificationV1),
        Argument(ArgumentType.Object),
    )
    def notify_on_overlap(
        self, layer_surface: object
    ) -> Proxy[ZcosmicOverlapNotificationV1]:
        """Get notified if a layer-shell surface is obstructed.

        Requests notifications for toplevels and layer-surfaces entering
        and leaving the surface-area of the given zwlr_layer_surface_v1.
        """
        return self._marshal_constructor(
            0, ZcosmicOverlapNotificationV1, layer_surface
        )


class ZcosmicOverlapNotifyV1Resource(Resource):
    interface = ZcosmicOverlapNotifyV1


class ZcosmicOverlapNotifyV1Global(Global):
    interface = ZcosmicOverlapNotifyV1


ZcosmicOverlapNotifyV1._gen_c()
ZcosmicOverlapNotifyV1.proxy_class = ZcosmicOverlapNotifyV1Proxy
ZcosmicOverlapNotifyV1.resource_class = ZcosmicOverlapNotifyV1Resource
ZcosmicOverlapNotifyV1.global_class = ZcosmicOverlapNotifyV1Global
