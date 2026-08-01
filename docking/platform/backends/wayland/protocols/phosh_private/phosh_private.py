# Minimal pywayland binding for phosh_private.get_thumbnail.
# ruff: noqa

from __future__ import annotations

from pywayland.protocol_core import (
    Argument,
    ArgumentType,
    Global,
    Interface,
    Proxy,
    Resource,
)
from pywayland.protocol.wayland import WlSurface

from ..wlr_foreign_toplevel_management_unstable_v1 import ZwlrForeignToplevelHandleV1
from .zwlr_screencopy_frame_v1 import ZwlrScreencopyFrameV1


class PhoshPrivateXdgSwitcher(Interface):
    name = "phosh_private_xdg_switcher"
    version = 7


class PhoshPrivateKeyboardEvent(Interface):
    name = "phosh_private_keyboard_event"
    version = 7


class PhoshPrivateStartupTracker(Interface):
    name = "phosh_private_startup_tracker"
    version = 7


class PhoshPrivate(Interface):
    name = "phosh_private"
    version = 7


class PhoshPrivateProxy(Proxy[PhoshPrivate]):
    interface = PhoshPrivate

    @PhoshPrivate.request(
        Argument(ArgumentType.Object, interface=WlSurface),
        Argument(ArgumentType.Uint),
    )
    def rotate_display(self, surface: WlSurface, degree: int) -> None:
        self._marshal(0, surface, degree)

    @PhoshPrivate.request(
        Argument(ArgumentType.NewId, interface=PhoshPrivateXdgSwitcher), version=2
    )
    def get_xdg_switcher(self) -> Proxy[PhoshPrivateXdgSwitcher]:
        return self._marshal_constructor(1, PhoshPrivateXdgSwitcher)

    @PhoshPrivate.request(
        Argument(ArgumentType.NewId, interface=ZwlrScreencopyFrameV1),
        Argument(ArgumentType.Object, interface=ZwlrForeignToplevelHandleV1),
        Argument(ArgumentType.Uint),
        Argument(ArgumentType.Uint),
        version=4,
    )
    def get_thumbnail(
        self,
        toplevel: ZwlrForeignToplevelHandleV1,
        max_width: int,
        max_height: int,
    ) -> Proxy[ZwlrScreencopyFrameV1]:
        return self._marshal_constructor(
            2, ZwlrScreencopyFrameV1, toplevel, max_width, max_height
        )

    @PhoshPrivate.request(
        Argument(ArgumentType.NewId, interface=PhoshPrivateKeyboardEvent), version=5
    )
    def get_keyboard_event(self) -> Proxy[PhoshPrivateKeyboardEvent]:
        return self._marshal_constructor(3, PhoshPrivateKeyboardEvent)

    @PhoshPrivate.request(
        Argument(ArgumentType.NewId, interface=PhoshPrivateStartupTracker), version=6
    )
    def get_startup_tracker(self) -> Proxy[PhoshPrivateStartupTracker]:
        return self._marshal_constructor(4, PhoshPrivateStartupTracker)

    @PhoshPrivate.request(Argument(ArgumentType.Uint), version=6)
    def set_shell_state(self, state: int) -> None:
        self._marshal(5, state)


class PhoshPrivateResource(Resource):
    interface = PhoshPrivate


class PhoshPrivateGlobal(Global):
    interface = PhoshPrivate


for interface in (
    PhoshPrivateXdgSwitcher,
    PhoshPrivateKeyboardEvent,
    PhoshPrivateStartupTracker,
    PhoshPrivate,
):
    interface._gen_c()

PhoshPrivate.proxy_class = PhoshPrivateProxy
PhoshPrivate.resource_class = PhoshPrivateResource
PhoshPrivate.global_class = PhoshPrivateGlobal
