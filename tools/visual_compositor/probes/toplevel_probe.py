"""Read native foreign toplevels from the private compositor, without Docking."""

from __future__ import annotations

import json
import struct
import sys

from pywayland.client import Display
from pywayland.protocol.ext_foreign_toplevel_list_v1 import ExtForeignToplevelListV1

from docking.platform.backends.wayland.protocols.cosmic_toplevel_info_v1 import (
    ZcosmicToplevelInfoV1,
)
from docking.platform.backends.wayland.protocols.ext_workspace_v1 import (
    ExtWorkspaceManagerV1,
)


def main() -> None:
    display = Display()
    windows = {}
    proxies = []
    spaces = {}
    info = None
    cosmic_handles = {}

    def attach_info(handle):
        if info is None or handle in cosmic_handles:
            return
        cosmic = info.get_cosmic_toplevel(handle)
        cosmic_handles[handle] = cosmic

        def state_changed(proxy, data):
            states = {value for (value,) in struct.iter_unpack("I", data)}
            if handle in windows:
                windows[handle].update(minimized=1 in states, active=2 in states)

        cosmic.dispatcher["state"] = state_changed

    def toplevel(manager, handle):
        windows[handle] = {"app_id": "", "title": ""}
        handle.dispatcher["app_id"] = lambda h, value: windows[h].update(app_id=value)
        handle.dispatcher["title"] = lambda h, value: windows[h].update(title=value)
        handle.dispatcher["closed"] = lambda h: windows.pop(h, None)
        attach_info(handle)

    def workspace(manager, handle):
        spaces[handle] = {"id": str(len(spaces)), "name": "", "active": False}
        handle.dispatcher["id"] = lambda h, value: spaces[h].update(id=value)
        handle.dispatcher["name"] = lambda h, value: spaces[h].update(name=value)
        handle.dispatcher["state"] = lambda h, value: spaces[h].update(
            active=bool(value & 1)
        )
        handle.dispatcher["removed"] = lambda h: spaces.pop(h, None)

    def global_added(registry, name, interface, version):
        nonlocal info
        if interface == ExtForeignToplevelListV1.name:
            manager = registry.bind(name, ExtForeignToplevelListV1, 1)
            manager.dispatcher["toplevel"] = toplevel
            proxies.append(manager)
        elif interface == ZcosmicToplevelInfoV1.name:
            info = registry.bind(name, ZcosmicToplevelInfoV1, min(version, 3))
            proxies.append(info)
            for handle in windows:
                attach_info(handle)
        elif interface == ExtWorkspaceManagerV1.name:
            manager = registry.bind(name, ExtWorkspaceManagerV1, 1)
            manager.dispatcher["workspace"] = workspace
            proxies.append(manager)

    try:
        display.connect()
        registry = display.get_registry()
        registry.dispatcher["global"] = global_added
        # Registry, handle announcements, then each handle's initial properties.
        for _ in range(5):
            display.roundtrip()
        for _ in range(20):
            if info is None or all("minimized" in w for w in windows.values()):
                break
            display.roundtrip()
        if not proxies:
            raise RuntimeError("compositor does not advertise foreign toplevel listing")
        print(
            json.dumps(
                list(
                    (
                        spaces
                        if len(sys.argv) > 1 and sys.argv[1] == "workspaces"
                        else windows
                    ).values()
                )
            )
        )
    finally:
        display.disconnect()


if __name__ == "__main__":
    main()
