from __future__ import annotations

from pathlib import Path

from docking.platform.applications.running import RunningAppInfo, RunningWindowInfo
from docking.platform.applications.types import (
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
)
from docking.platform.backends.base import WindowId


def test_running_app_info_aggregates_canonical_window_values():
    runtime_app = ApplicationInfo(
        desktop_id="runtime-demo.desktop",
        name="Demo",
        declared_icon="demo",
        wm_class="Demo",
        exec_line="/opt/demo/bin/demo",
        origin=ApplicationOrigin.RUNTIME,
        location=ApplicationLocation.SANDBOX,
        desktop_file=None,
        executable_path=Path("/opt/demo/bin/demo"),
        aliases=("demo",),
        visible=True,
        has_gio_source=False,
    )
    windows = (
        RunningWindowInfo(
            desktop_id=runtime_app.desktop_id,
            xid=11,
            window_id=WindowId.x11(11),
            active=True,
            urgent=False,
            window="first",
            runtime_app=runtime_app,
        ),
        RunningWindowInfo(
            desktop_id=runtime_app.desktop_id,
            xid=12,
            window_id=WindowId.x11(12),
            active=False,
            urgent=True,
            window="second",
        ),
    )

    running = RunningAppInfo.from_windows(windows)

    assert running.count == 2
    assert running.active is True
    assert running.urgent is True
    assert running.xids == (11, 12)
    assert running.window_ids == (WindowId.x11(11), WindowId.x11(12))
    assert running.runtime_app is runtime_app
