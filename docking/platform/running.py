"""Compatibility values for canonical running-application state."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docking.platform.applications import entries as desktop_entries
from docking.platform.applications.running import (
    RunningAppInfo as _CanonicalRunningAppInfo,
)
from docking.platform.applications.running import (
    RunningWindowInfo as _CanonicalRunningWindowInfo,
)
from docking.platform.applications.types import (
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
)
from docking.platform.backends.base import WindowId


@dataclass(frozen=True)
class RuntimeAppIdentity:
    """Historical runtime identity retained only for import compatibility."""

    desktop_id: str
    executable_path: str
    name: str
    icon_name: str
    wm_class: str


def _canonical_runtime_application(
    runtime_app: RuntimeAppIdentity | ApplicationInfo | None,
) -> ApplicationInfo | None:
    """Convert a legacy runtime identity at the compatibility boundary."""
    if runtime_app is None or isinstance(runtime_app, ApplicationInfo):
        return runtime_app
    executable = runtime_app.executable_path
    executable_path = Path(executable).expanduser() if executable else None
    return ApplicationInfo(
        desktop_id=runtime_app.desktop_id,
        name=runtime_app.name or runtime_app.desktop_id,
        declared_icon=runtime_app.icon_name,
        wm_class=runtime_app.wm_class,
        exec_line=executable,
        origin=ApplicationOrigin.RUNTIME,
        location=ApplicationLocation.SANDBOX,
        desktop_file=None,
        executable_path=executable_path,
        aliases=tuple(
            desktop_entries.match_aliases(
                desktop_id=runtime_app.desktop_id,
                wm_class=runtime_app.wm_class,
                exec_line=executable,
            )
        ),
        visible=True,
        has_gio_source=False,
    )


def _legacy_runtime_identity(
    application: ApplicationInfo | None,
) -> RuntimeAppIdentity | None:
    """Project canonical runtime metadata onto the historical value."""
    if application is None or application.origin is not ApplicationOrigin.RUNTIME:
        return None
    executable = (
        str(application.executable_path)
        if application.executable_path is not None
        else application.exec_line
    )
    return RuntimeAppIdentity(
        desktop_id=application.desktop_id,
        executable_path=executable,
        name=application.name,
        icon_name=application.declared_icon,
        wm_class=application.wm_class,
    )


class RunningWindowInfo(_CanonicalRunningWindowInfo):
    """Legacy constructor that publishes canonical runtime metadata."""

    __slots__ = ()

    def __init__(
        self,
        desktop_id: str,
        xid: int,
        window_id: WindowId,
        active: bool,
        urgent: bool,
        window: Any,
        runtime_app: RuntimeAppIdentity | ApplicationInfo | None = None,
    ) -> None:
        super().__init__(
            desktop_id=desktop_id,
            xid=xid,
            window_id=window_id,
            active=active,
            urgent=urgent,
            window=window,
            runtime_app=_canonical_runtime_application(runtime_app),
        )


class RunningAppInfo(_CanonicalRunningAppInfo):
    """Legacy aggregate constructor with a canonical runtime boundary."""

    __slots__ = ()

    def __init__(
        self,
        count: int = 0,
        active: bool = False,
        urgent: bool = False,
        windows: tuple[Any, ...] = (),
        xids: tuple[int, ...] = (),
        window_ids: tuple[WindowId, ...] = (),
        runtime_app: RuntimeAppIdentity | ApplicationInfo | None = None,
    ) -> None:
        super().__init__(
            count=count,
            active=active,
            urgent=urgent,
            windows=windows,
            xids=xids,
            window_ids=window_ids,
            runtime_app=_canonical_runtime_application(runtime_app),
        )

    @classmethod
    def from_windows(
        cls,
        windows: Iterable[_CanonicalRunningWindowInfo],
    ) -> RunningAppInfo:
        """Aggregate legacy or canonical windows into canonical model values."""
        snapshots = tuple(windows)
        runtime_app = next(
            (
                application
                for snapshot in snapshots
                if (application := _canonical_runtime_application(snapshot.runtime_app))
                is not None
                and application.origin is ApplicationOrigin.RUNTIME
            ),
            None,
        )
        return cls(
            count=len(snapshots),
            active=any(snapshot.active for snapshot in snapshots),
            urgent=any(snapshot.urgent for snapshot in snapshots),
            windows=tuple(snapshot.window for snapshot in snapshots),
            xids=tuple(snapshot.xid for snapshot in snapshots),
            window_ids=tuple(snapshot.window_id for snapshot in snapshots),
            runtime_app=runtime_app,
        )


__all__ = [
    "RunningAppInfo",
    "RunningWindowInfo",
    "RuntimeAppIdentity",
]
