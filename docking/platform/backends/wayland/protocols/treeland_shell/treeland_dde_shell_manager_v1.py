# Minimal pywayland bindings for Treeland's window overlap checker.
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
from pywayland.protocol.wayland import WlOutput


class TreelandWindowOverlapChecker(Interface):
    name = "treeland_window_overlap_checker"
    version = 1


class TreelandWindowOverlapCheckerProxy(Proxy[TreelandWindowOverlapChecker]):
    interface = TreelandWindowOverlapChecker

    @TreelandWindowOverlapChecker.request(
        Argument(ArgumentType.Int),
        Argument(ArgumentType.Int),
        Argument(ArgumentType.Uint),
        Argument(ArgumentType.Object, interface=WlOutput),
    )
    def update(self, width: int, height: int, anchor: int, output: WlOutput) -> None:
        self._marshal(0, width, height, anchor, output)

    @TreelandWindowOverlapChecker.request()
    def destroy(self) -> None:
        self._marshal(1)
        self._destroy()


class TreelandWindowOverlapCheckerResource(Resource):
    interface = TreelandWindowOverlapChecker

    @TreelandWindowOverlapChecker.event()
    def enter(self) -> None:
        self._post_event(0)

    @TreelandWindowOverlapChecker.event()
    def leave(self) -> None:
        self._post_event(1)


class TreelandWindowOverlapCheckerGlobal(Global):
    interface = TreelandWindowOverlapChecker


TreelandWindowOverlapChecker._gen_c()
TreelandWindowOverlapChecker.proxy_class = TreelandWindowOverlapCheckerProxy
TreelandWindowOverlapChecker.resource_class = TreelandWindowOverlapCheckerResource
TreelandWindowOverlapChecker.global_class = TreelandWindowOverlapCheckerGlobal


class TreelandDDEShellManagerV1(Interface):
    name = "treeland_dde_shell_manager_v1"
    version = 2


class TreelandDDEShellManagerV1Proxy(Proxy[TreelandDDEShellManagerV1]):
    interface = TreelandDDEShellManagerV1

    @TreelandDDEShellManagerV1.request(
        Argument(ArgumentType.NewId, interface=TreelandWindowOverlapChecker)
    )
    def get_window_overlap_checker(self) -> Proxy[TreelandWindowOverlapChecker]:
        return self._marshal_constructor(0, TreelandWindowOverlapChecker)


class TreelandDDEShellManagerV1Resource(Resource):
    interface = TreelandDDEShellManagerV1


class TreelandDDEShellManagerV1Global(Global):
    interface = TreelandDDEShellManagerV1


TreelandDDEShellManagerV1._gen_c()
TreelandDDEShellManagerV1.proxy_class = TreelandDDEShellManagerV1Proxy
TreelandDDEShellManagerV1.resource_class = TreelandDDEShellManagerV1Resource
TreelandDDEShellManagerV1.global_class = TreelandDDEShellManagerV1Global
