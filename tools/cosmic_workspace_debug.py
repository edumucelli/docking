#!/usr/bin/env python3
"""Debug COSMIC workspace protocol binding."""
from __future__ import annotations
import sys, time
sys.path.insert(0, ".")

from pywayland.client import Display
from gi.repository import GLib

# Test: direct protocol binding without adapters
print("=== Direct workspace protocol binding ===")
display = Display()
display.connect()
registry = display.get_registry()

manager_v2_name = None
manager_v2_version = None
all_globals = []

def on_global(reg, name, interface, version):
    all_globals.append((interface, version, name))

registry.dispatcher["global"] = on_global
display.dispatch(block=False)
display.roundtrip()

print(f"Globals found: {len(all_globals)}")
for iface, ver, name in all_globals:
    if "workspace" in iface.lower():
        print(f"  {iface} v{ver} (name={name})")
        if iface == "zcosmic_workspace_manager_v2":
            manager_v2_name = name
            manager_v2_version = ver

if manager_v2_name is None:
    print("FAIL: zcosmic_workspace_manager_v2 not found")
    display.disconnect()
    sys.exit(1)

print(f"\nBinding zcosmic_workspace_manager_v2 name={manager_v2_name} v{manager_v2_version}")

from docking.platform.backends.wayland.protocols.cosmic_workspace_v1 import (
    ZcosmicWorkspaceManagerV2,
)
bind_version = min(manager_v2_version, ZcosmicWorkspaceManagerV2.version)
print(f"Bind version: {bind_version}")

manager = registry.bind(manager_v2_name, ZcosmicWorkspaceManagerV2, bind_version)

groups = []
workspaces = []
done_flag = [False]

def on_workspace_group(mgr, group):
    print(f"  >>> workspace_group event: {group}")
    groups.append(group)
    def on_ws(g, ws):
        print(f"    >>> workspace event: {ws}")
        workspaces.append(ws)
    group.dispatcher["workspace"] = on_ws

def on_done(mgr):
    print("  >>> done event")
    done_flag[0] = True

manager.dispatcher["workspace_group"] = on_workspace_group
manager.dispatcher["done"] = on_done

display.flush()
display.roundtrip()
display.flush()

# Pump GLib events
ctx = GLib.MainContext.default()
for _ in range(30):
    ctx.iteration(may_block=False)
time.sleep(0.3)
display.dispatch(block=False)
display.roundtrip()

for _ in range(10):
    ctx.iteration(may_block=False)

print(f"\nGroups received: {len(groups)}")
print(f"Workspaces received: {len(workspaces)}")
print(f"Done received: {done_flag[0]}")

display.disconnect()
print("\nDone")
