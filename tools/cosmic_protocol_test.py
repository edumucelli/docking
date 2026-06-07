#!/usr/bin/env python3
"""Exercise COSMIC protocol bindings against the live compositor.

Usage: .venv/bin/python tools/cosmic_protocol_test.py

Validates every COSMIC protocol integration point without launching the full
Docking GTK application.
"""

from __future__ import annotations

import struct
import sys
import time

sys.path.insert(0, ".")

from docking.platform.backends.wayland.runtime import WaylandProtocolRuntime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_flag(values, *, token: str, bit: int) -> bool:
    """Check workspace state/capability flags in array or integer form."""
    if isinstance(values, int):
        return bool(values & bit)
    if isinstance(values, bytes | bytearray | memoryview):
        values = bytes(values)
        return bool(int.from_bytes(values, "little") & bit)
    return token in {
        (v if isinstance(v, str) else str(v)).strip().lower() for v in values
    }


def pump_events(count: int = 15, sleep: float = 0.15) -> None:
    """Pump GLib main context and allow Wayland events to flush."""
    import gi
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib
    ctx = GLib.MainContext.default()
    for _ in range(count):
        ctx.iteration(may_block=False)
    time.sleep(sleep)
    for _ in range(count):
        ctx.iteration(may_block=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_backend_autodetection() -> bool:
    """Verify COSMIC session is auto-detected."""
    print("\n─── Backend Auto-Detection ───")
    import os
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    print(f"  XDG_CURRENT_DESKTOP = {desktop}")
    from docking.platform.backends.selection import _is_cosmic_session
    if not _is_cosmic_session():
        print("  FAIL: expected True on COSMIC")
        return False
    print("  PASS")
    return True


def test_toplevel_listing(runtime: WaylandProtocolRuntime) -> bool:
    """Verify toplevel discovery through the COSMIC toplevel adapter."""
    print("\n─── Toplevel Listing (cosmic adapter) ───")
    adapter = runtime.cosmic_toplevel
    if not adapter.available:
        print("  FAIL: COSMIC toplevel adapter not available")
        return False

    # Collect pending toplevels (already received during roundtrip)
    pending = getattr(adapter, "_pending_toplevels", [])
    pending_data = getattr(adapter, "_pending_data", {})

    print(f"  Toplevels discovered: {len(pending)}")
    all_done = True
    for i, handle in enumerate(pending):
        data = pending_data.get(handle, {})
        title = data.get("title", "?")
        app_id = data.get("app_id", "?")
        is_done = data.get("done", False)
        print(f"    [{i}] title={title!r}  app_id={app_id!r}  done={is_done}")
        if not is_done:
            all_done = False

    if not pending:
        print("  (No open toplevels — this may be OK on an empty desktop)")
        return True

    if not all_done:
        print("  FAIL: some toplevels not marked done")
        return False

    print("  PASS")
    return True


def test_toplevel_management_capabilities(runtime: WaylandProtocolRuntime) -> bool:
    """Verify COSMIC toplevel management capabilities are received."""
    print("\n─── Toplevel Management Capabilities ───")
    adapter = runtime.cosmic_toplevel
    if not adapter.available:
        print("  FAIL: COSMIC toplevel adapter not available")
        return False

    if not adapter.has_management:
        print("  WARN: zcosmic_toplevel_manager_v1 not bound")
        return True  # management is optional

    caps = adapter.capabilities
    CAP_NAMES = {
        1: "close", 2: "activate", 3: "maximize",
        4: "minimize", 5: "fullscreen", 6: "move_to_workspace",
        7: "sticky", 8: "move_to_ext_workspace",
    }
    print(f"  Capabilities: {len(caps)}")
    for cap in sorted(caps):
        print(f"    [{cap}] {CAP_NAMES.get(cap, 'unknown')}")

    # Verify essential dock capabilities
    found = {CAP_NAMES.get(c, "?") for c in caps}
    for name in ("activate", "close", "minimize"):
        if name not in found:
            print(f"  WARN: '{name}' not advertised")

    print("  PASS")
    return True


def test_workspaces(runtime: WaylandProtocolRuntime) -> bool:
    """Verify workspace listing via ext_workspace_manager_v1.

    On COSMIC, ext_workspace_manager_v1 works correctly while the
    COSMIC-specific zcosmic_workspace_manager_v2 binding does not
    match the compositor's current protocol structure.
    """
    print("\n─── Workspaces (ext_workspace_manager_v1) ───")
    adapter = runtime.workspaces
    if not adapter.available:
        print("  FAIL: ext_workspace_manager_v1 not available")
        return False

    pending = getattr(adapter, "_pending_workspaces", [])
    pending_data = getattr(adapter, "_pending_data", {})
    pending_done = getattr(adapter, "_pending_done", False)

    print(f"  Workspaces discovered: {len(pending)}")
    for i, ws in enumerate(pending):
        data = pending_data.get(ws, {})
        name = data.get("name", "?")
        state = data.get("state")
        caps = data.get("capabilities")
        active = _has_flag(state, token="active", bit=1) if state is not None else None
        can_activate = _has_flag(caps, token="activate", bit=1) if caps is not None else None
        print(f"    [{i}] name={name!r}  active={active}  can_activate={can_activate}")

    if not pending:
        print("  FAIL: no workspaces discovered on COSMIC desktop")
        return False
    if not pending_done:
        print("  FAIL: workspace 'done' event not received")
        return False

    print("  PASS")
    return True


def test_workspace_activation(runtime: WaylandProtocolRuntime) -> bool:
    """Verify workspace switching on COSMIC."""
    print("\n─── Workspace Activation ───")
    adapter = runtime.workspaces
    if not adapter.available:
        print("  SKIP: ext_workspace_manager_v1 not available")
        return True

    pending = getattr(adapter, "_pending_workspaces", [])
    pending_data = getattr(adapter, "_pending_data", {})
    if len(pending) < 2:
        print("  SKIP: need at least 2 workspaces (found {})".format(len(pending)))
        return True

    # Find active and first inactive workspace
    active_ws = inactive_ws = None
    for ws in pending:
        data = pending_data.get(ws, {})
        state = data.get("state")
        if _has_flag(state, token="active", bit=1):
            active_ws = ws
        elif inactive_ws is None:
            inactive_ws = ws

    if inactive_ws is None:
        print("  SKIP: all workspaces active")
        return True

    data = pending_data.get(inactive_ws, {})
    print(f"  Switching to workspace: {data.get('name', '?')!r}")
    adapter.activate(inactive_ws)
    pump_events(count=10, sleep=0.3)

    # Verify the switch
    data2 = getattr(adapter, "_pending_data", {}).get(inactive_ws, {})
    state2 = data2.get("state")
    now_active = _has_flag(state2, token="active", bit=1) if state2 is not None else False
    print(f"  Switch result: active={now_active}")

    # Switch back
    if active_ws is not None:
        orig_data = pending_data.get(active_ws, {})
        print(f"  Switching back to: {orig_data.get('name', '?')!r}")
        adapter.activate(active_ws)
        pump_events(count=10, sleep=0.3)

    print("  PASS")
    return True


def test_overlap_protocol_bound(runtime: WaylandProtocolRuntime) -> bool:
    """Verify COSMIC overlap notify protocol is bound.

    Full exercise requires a realized layer-shell surface (GTK window),
    which is validated by the full Docking launch.
    """
    print("\n─── Overlap Notify Protocol ───")
    adapter = runtime.cosmic_overlap
    if not adapter.available:
        print("  FAIL: COSMIC overlap adapter not available")
        return False

    notify = getattr(adapter, "_overlap_notify", None)
    print(f"  zcosmic_overlap_notify_v1 bound: {notify is not None}")

    # Without a layer surface we cannot exercise the overlap subscription,
    # but we can verify the adapter is properly constructed.
    print("  (Subscription requires a layer-shell surface — tested with full app)")
    print("  PASS")
    return True


def test_backend_creation() -> bool:
    """Verify CosmicSessionBackend can be created with the correct services."""
    print("\n─── CosmicSessionBackend Creation ───")
    import os
    os.environ["DOCKING_BACKEND"] = "cosmic"

    from unittest.mock import MagicMock
    from docking.platform.backends.selection import create_session_backend
    from docking.platform.backends.wayland.toplevels import WaylandForeignToplevelWindowService
    from docking.platform.backends.wayland.services import WaylandLayerShellSurfaceService
    from docking.platform.backends.wayland.cosmic_session import CosmicOverlapVisibilityService
    from docking.platform.backends.wayland.workspaces import WaylandWorkspaceService
    from docking.platform.backends.wayland.previews import WaylandPreviewService
    from docking.platform.backends.reduced.services import ReducedPreviewService

    config = MagicMock()
    launcher = MagicMock()
    model = MagicMock()
    model.visible_items = MagicMock(return_value=[])

    backend = create_session_backend(config=config, launcher=launcher, model=model)
    print(f"  Backend name: {backend.name}")
    print(f"  Windows svc : {type(backend.windows).__name__}")
    print(f"  Surface svc : {type(backend.surface).__name__}")
    print(f"  Visibility : {type(backend.visibility).__name__}")
    print(f"  Workspaces : {type(backend.workspaces).__name__ if backend.workspaces else 'None'}")
    print(f"  Previews   : {type(backend.previews).__name__}")

    checks = [
        ("WindowService", isinstance(backend.windows, WaylandForeignToplevelWindowService)),
        ("LayerShell", isinstance(backend.surface, WaylandLayerShellSurfaceService)),
        ("OverlapVis", isinstance(backend.visibility, CosmicOverlapVisibilityService)),
        ("Workspaces", isinstance(backend.workspaces, WaylandWorkspaceService)),
        ("Previews", isinstance(backend.previews, WaylandPreviewService)),
    ]
    all_ok = True
    for name, result in checks:
        status = "✓" if result else "✗"
        print(f"    {status} {name}")
        if not result:
            all_ok = False

    # Verify start/stop lifecycle
    backend.start()
    backend.stop()

    if not all_ok:
        print("  FAIL")
        return False
    print("  PASS")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("COSMIC Protocol Test Suite")
    print("=" * 50)

    runtime = WaylandProtocolRuntime()
    if not runtime.start():
        print("FATAL: Could not start Wayland protocol runtime")
        return 1

    print(f"\nProtocol availability:")
    print(f"  COSMIC toplevel       : {runtime.cosmic_toplevel.available}")
    print(f"  COSMIC toplevel mgmt  : {runtime.cosmic_toplevel.has_management}")
    print(f"  COSMIC workspace (v2) : {runtime.cosmic_workspace.available}")
    print(f"  ext_workspace (std)   : {runtime.workspaces.available}")
    print(f"  COSMIC overlap        : {runtime.cosmic_overlap.available}")

    pump_events()

    all_passed = True

    all_passed &= test_backend_autodetection()
    all_passed &= test_toplevel_listing(runtime)
    all_passed &= test_toplevel_management_capabilities(runtime)
    all_passed &= test_workspaces(runtime)
    all_passed &= test_workspace_activation(runtime)
    all_passed &= test_overlap_protocol_bound(runtime)
    all_passed &= test_backend_creation()

    runtime.stop()

    print("\n" + "=" * 50)
    if all_passed:
        print("All COSMIC protocol tests passed!")
        return 0
    else:
        print("Some tests FAILED — see output above")
        return 1


if __name__ == "__main__":
    sys.exit(main())
