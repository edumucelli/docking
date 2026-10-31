"""Wayfire IPC contracts for two outputs and missing optional action plugins."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GLib

if sys.argv[1] == "--fixture":
    GLib.set_prgname("lab-wayfire-contract")
from gi.repository import GdkPixbuf, Gtk

if sys.argv[1] == "--fixture":
    window = Gtk.Window(title="Wayfire multi-output regression")
    window.set_decorated(False)
    window.set_default_size(200, 150)
    area = Gtk.DrawingArea()

    def draw(_widget, cr):
        cr.set_source_rgb(1, 0, 0)
        cr.paint()
        return False

    area.connect("draw", draw)
    window.add(area)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    Gtk.main()
    raise SystemExit
from docking.core.config import Config
from docking.core.items import DockItem
from docking.platform.applications.identity import (
    LaunchProvenanceStore,
    ProcessIdentityService,
)
from docking.platform.applications.registry import ApplicationRegistry
from docking.platform.backends.base import ActionResult, Rect
from docking.platform.backends.wayland.wayfire_ipc import (
    WayfireDesktopActionService,
    WayfireIpcClient,
    WayfireVisibilityMonitor,
    WayfireWindowPickService,
    WayfireWindowService,
    WayfireWorkspaceService,
    load_wayfire_desktop_action_service,
)

client = WayfireIpcClient(socket_path=os.environ["WAYFIRE_SOCKET"])
outputs = cast(list[dict[str, Any]], client.request("window-rules/list-outputs"))
assert isinstance(outputs, list)
assert len(outputs) == 2, outputs
second = next(row for row in outputs if row["geometry"]["x"] != 0)
assert second["geometry"]["x"] == 800, outputs
methods = client.request("list-methods")
Path(os.environ["LAB_DIR"], f"{sys.argv[1]}-ipc.json").write_text(
    json.dumps({"outputs": outputs, "methods": methods}, indent=2)
)
full = sys.argv[1] == "full"
desktop_path = (
    Path(os.environ["XDG_DATA_HOME"]) / "applications" / "lab-wayfire-contract.desktop"
)
desktop_path.parent.mkdir(parents=True, exist_ok=True)
desktop_path.write_text(
    "[Desktop Entry]\nType=Application\nName=Wayfire contract fixture\n"
    "Exec=true\nStartupWMClass=lab-wayfire-contract\nIcon=utilities-terminal\n"
)
from docking.platform.composition.selection import platform_provider

provider = platform_provider()
registry = ApplicationRegistry(
    catalog_backend=provider.catalog(),
    association_backend=provider.associations(),
    registration_backend=provider.registrations(),
)
assert registry.refresh()
model = SimpleNamespace(
    visible_items=lambda: [
        DockItem(
            desktop_id="lab-wayfire-contract.desktop", wm_class="lab-wayfire-contract"
        )
    ],
    update_running=lambda **kwargs: None,
)
windows = WayfireWindowService(
    model=model,
    application_registry=registry,
    process_identity_service=ProcessIdentityService(
        LaunchProvenanceStore(), backend=provider.processes()
    ),
    client=client,
    config=Config(current_workspace_only=True),
)
picker = WayfireWindowPickService(client=client)
workspaces = WayfireWorkspaceService(client=client)
child = subprocess.Popen([sys.executable, __file__, "--fixture"])
try:

    def wait(predicate):
        end = time.monotonic() + 8
        while time.monotonic() < end:
            windows._refresh()
            if predicate():
                return
            while GLib.MainContext.default().iteration(False):
                pass
            time.sleep(0.02)
        raise AssertionError(windows.list_all_windows())

    wait(lambda: bool(windows.list_all_windows()))
    row = next(
        row
        for row in windows.list_all_windows()
        if row.title == "Wayfire multi-output regression"
    )
    view_id = int(row.id.value)
    assert client.action(
        "window-rules/configure-view",
        {
            "id": view_id,
            "output_id": second["id"],
            "geometry": {"x": 10, "y": 20, "width": 200, "height": 150},
        },
    ).succeeded
    wait(lambda: windows.list_all_windows()[0].geometry.x == 810)
    row = windows.list_all_windows()[0]
    assert row.geometry == Rect(810, 20, 200, 150), row
    assert row.workspace_id == f"{second['id']}:0,0", row
    assert row.on_current_workspace is True and row.visible is True
    assert picker.pick_window_at(x=820, y=30).id == row.id
    assert picker.pick_window_at(x=20, y=30) is None
    capture = Path(os.environ["LAB_DIR"]) / f"{sys.argv[1]}-global-geometry.png"
    subprocess.run(["grim", "-s", "1", str(capture)], check=True, timeout=5)
    image = GdkPixbuf.Pixbuf.new_from_file(str(capture))
    offset = 30 * image.get_rowstride() + 820 * image.get_n_channels()
    assert image.get_pixels()[offset : offset + 3] == b"\xff\x00\x00"
    assert windows.activate(row.id).succeeded
    raw = cast(list[dict[str, Any]], client.request("window-rules/list-views"))
    assert isinstance(raw, list)
    assert next(view for view in raw if view["id"] == view_id)["geometry"]["x"] == 10
    monitor = WayfireVisibilityMonitor(
        client=client,
        config=SimpleNamespace(hide_mode_enum="dodge-active"),
        get_dock_rect=lambda: Rect(810, 20, 200, 50),
        on_change=lambda _: None,
    )
    assert monitor._evaluate() is True
    assert windows.supports_minimize is full
    assert workspaces.supports_switch is full
    workspaces._refresh()
    assert row.workspace_id in {
        workspace.id for workspace in workspaces.list_workspaces()
    }
    if full:
        assert windows.minimize_all("lab-wayfire-contract.desktop").succeeded
        wait(lambda: windows.list_all_windows()[0].minimized)
        assert monitor._evaluate() is False
        assert windows.activate(row.id).succeeded
        wait(lambda: not windows.list_all_windows()[0].minimized)
        assert workspaces.activate(f"{second['id']}:1,0").succeeded
        wait(lambda: windows.list_all_windows()[0].visible is False)
        assert windows.list_all_windows()[0].workspace_id == row.workspace_id
        assert not windows.list_windows("lab-wayfire-contract.desktop")
        assert picker.pick_window_at(x=20, y=30) is None
        assert monitor._evaluate() is False
        assert workspaces.activate(row.workspace_id).succeeded
        wait(lambda: windows.list_all_windows()[0].visible is True)
        assert WayfireDesktopActionService(client=client).show_desktop().succeeded
    else:
        assert (
            windows.minimize_all("lab-wayfire-contract.desktop")
            is ActionResult.UNSUPPORTED
        )
        assert workspaces.activate(f"{second['id']}:1,0") is ActionResult.UNSUPPORTED
        assert load_wayfire_desktop_action_service() is None
    assert windows.close(row.id).succeeded
    wait(lambda: not windows.list_all_windows())
    print(
        json.dumps(
            {
                "mode": sys.argv[1],
                "multi_output_geometry": True,
                "picker_global_coordinates": True,
                "workspace_cell_identity": True,
                "current_workspace_scope": True,
                "plugin_capabilities": True,
                "native_actions": True,
            }
        ),
        flush=True,
    )
finally:
    windows.stop()
    picker.stop()
    workspaces.stop()
    if child.poll() is None:
        child.terminate()
    child.wait(timeout=3)
