from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from docking.platform.applications.running import RunningAppInfo, RunningWindowInfo
from docking.platform.applications.types import (
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
)
from docking.platform.backends.base import WindowId
from docking.platform.running import (
    RunningAppInfo as LegacyRunningAppInfo,
)
from docking.platform.running import (
    RunningWindowInfo as LegacyRunningWindowInfo,
)
from docking.platform.running import RuntimeAppIdentity


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


def test_runtime_identity_compatibility_value_preserves_legacy_type_and_fields():
    positional = RuntimeAppIdentity(
        "runtime-demo.desktop",
        "/opt/demo/bin/demo",
        "Demo",
        "demo",
        "Demo",
    )
    keyword = RuntimeAppIdentity(
        desktop_id="runtime-demo.desktop",
        executable_path="/opt/demo/bin/demo",
        name="Demo",
        icon_name="demo",
        wm_class="Demo",
    )

    assert type(positional) is RuntimeAppIdentity
    assert positional == keyword
    assert positional.executable_path == "/opt/demo/bin/demo"
    assert not isinstance(positional, ApplicationInfo)
    with pytest.raises(FrozenInstanceError):
        positional.name = "Changed"  # type: ignore[misc]


def test_legacy_running_constructors_canonicalize_runtime_metadata():
    legacy_runtime = RuntimeAppIdentity(
        "runtime-demo.desktop",
        "/opt/demo/bin/demo",
        "Demo",
        "demo",
        "Demo",
    )

    window = LegacyRunningWindowInfo(
        legacy_runtime.desktop_id,
        11,
        WindowId.x11(11),
        True,
        False,
        "window",
        legacy_runtime,
    )
    direct = LegacyRunningAppInfo(
        1,
        True,
        False,
        ("window",),
        (11,),
        (WindowId.x11(11),),
        legacy_runtime,
    )
    aggregate = LegacyRunningAppInfo.from_windows((window,))

    assert isinstance(window, RunningWindowInfo)
    assert isinstance(direct, RunningAppInfo)
    assert isinstance(aggregate, RunningAppInfo)
    assert isinstance(window.runtime_app, ApplicationInfo)
    assert isinstance(direct.runtime_app, ApplicationInfo)
    assert isinstance(aggregate.runtime_app, ApplicationInfo)
    assert aggregate.runtime_app.origin is ApplicationOrigin.RUNTIME
    assert aggregate.runtime_app.executable_path == Path("/opt/demo/bin/demo")


def test_legacy_from_windows_repairs_a_legacy_value_in_canonical_window_shape():
    legacy_runtime = RuntimeAppIdentity(
        "runtime-demo.desktop",
        "/opt/demo/bin/demo",
        "Demo",
        "demo",
        "Demo",
    )
    window = RunningWindowInfo(
        desktop_id=legacy_runtime.desktop_id,
        xid=11,
        window_id=WindowId.x11(11),
        active=False,
        urgent=True,
        window="window",
        runtime_app=legacy_runtime,  # type: ignore[arg-type]
    )

    aggregate = LegacyRunningAppInfo.from_windows((window,))

    assert isinstance(aggregate.runtime_app, ApplicationInfo)
    assert aggregate.runtime_app.executable_path == Path("/opt/demo/bin/demo")
