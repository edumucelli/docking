# Minimal pywayland binding for treeland_window_management_v1.
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


class TreelandWindowManagementV1(Interface):
    name = "treeland_window_management_v1"
    version = 1


class TreelandWindowManagementV1Proxy(Proxy[TreelandWindowManagementV1]):
    interface = TreelandWindowManagementV1

    @TreelandWindowManagementV1.request(Argument(ArgumentType.Uint))
    def set_desktop(self, state: int) -> None:
        self._marshal(0, state)


class TreelandWindowManagementV1Resource(Resource):
    interface = TreelandWindowManagementV1

    @TreelandWindowManagementV1.event(Argument(ArgumentType.Uint))
    def show_desktop(self, state: int) -> None:
        self._post_event(0, state)


class TreelandWindowManagementV1Global(Global):
    interface = TreelandWindowManagementV1


TreelandWindowManagementV1._gen_c()
TreelandWindowManagementV1.proxy_class = TreelandWindowManagementV1Proxy
TreelandWindowManagementV1.resource_class = TreelandWindowManagementV1Resource
TreelandWindowManagementV1.global_class = TreelandWindowManagementV1Global
