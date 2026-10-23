# Minimal pywayland binding for zwlr_screencopy_frame_v1 used by phosh_private.
# ruff: noqa

from __future__ import annotations

import enum

from pywayland.protocol_core import (
    Argument,
    ArgumentType,
    Global,
    Interface,
    Proxy,
    Resource,
)
from pywayland.protocol.wayland import WlBuffer


class ZwlrScreencopyFrameV1(Interface):
    name = "zwlr_screencopy_frame_v1"
    version = 3

    class flags(enum.IntFlag):
        y_invert = 1


class ZwlrScreencopyFrameV1Proxy(Proxy[ZwlrScreencopyFrameV1]):
    interface = ZwlrScreencopyFrameV1

    @ZwlrScreencopyFrameV1.request(Argument(ArgumentType.Object, interface=WlBuffer))
    def copy(self, buffer: WlBuffer) -> None:
        self._marshal(0, buffer)

    @ZwlrScreencopyFrameV1.request()
    def destroy(self) -> None:
        self._marshal(1)
        self._destroy()

    @ZwlrScreencopyFrameV1.request(
        Argument(ArgumentType.Object, interface=WlBuffer), version=2
    )
    def copy_with_damage(self, buffer: WlBuffer) -> None:
        self._marshal(2, buffer)


class ZwlrScreencopyFrameV1Resource(Resource):
    interface = ZwlrScreencopyFrameV1

    @ZwlrScreencopyFrameV1.event(
        Argument(ArgumentType.Uint),
        Argument(ArgumentType.Uint),
        Argument(ArgumentType.Uint),
        Argument(ArgumentType.Uint),
    )
    def buffer(self, format_: int, width: int, height: int, stride: int) -> None:
        self._post_event(0, format_, width, height, stride)

    @ZwlrScreencopyFrameV1.event(Argument(ArgumentType.Uint))
    def flags(self, flags: int) -> None:
        self._post_event(1, flags)

    @ZwlrScreencopyFrameV1.event(
        Argument(ArgumentType.Uint),
        Argument(ArgumentType.Uint),
        Argument(ArgumentType.Uint),
    )
    def ready(self, tv_sec_hi: int, tv_sec_lo: int, tv_nsec: int) -> None:
        self._post_event(2, tv_sec_hi, tv_sec_lo, tv_nsec)

    @ZwlrScreencopyFrameV1.event()
    def failed(self) -> None:
        self._post_event(3)

    @ZwlrScreencopyFrameV1.event(
        Argument(ArgumentType.Uint),
        Argument(ArgumentType.Uint),
        Argument(ArgumentType.Uint),
        Argument(ArgumentType.Uint),
        version=2,
    )
    def damage(self, x: int, y: int, width: int, height: int) -> None:
        self._post_event(4, x, y, width, height)

    @ZwlrScreencopyFrameV1.event(
        Argument(ArgumentType.Uint),
        Argument(ArgumentType.Uint),
        Argument(ArgumentType.Uint),
        version=3,
    )
    def linux_dmabuf(self, format_: int, width: int, height: int) -> None:
        self._post_event(5, format_, width, height)

    @ZwlrScreencopyFrameV1.event(version=3)
    def buffer_done(self) -> None:
        self._post_event(6)


class ZwlrScreencopyFrameV1Global(Global):
    interface = ZwlrScreencopyFrameV1


ZwlrScreencopyFrameV1._gen_c()
ZwlrScreencopyFrameV1.proxy_class = ZwlrScreencopyFrameV1Proxy
ZwlrScreencopyFrameV1.resource_class = ZwlrScreencopyFrameV1Resource
ZwlrScreencopyFrameV1.global_class = ZwlrScreencopyFrameV1Global
