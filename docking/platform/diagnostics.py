# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Runtime diagnostics collection and report formatting."""

from __future__ import annotations

import importlib.util
import os
import platform
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from docking import __version__
from docking.platform.applications.types import ApplicationDiscoveryDiagnostic
from docking.platform.backends.base import DisplayServer, PlatformCapabilities
from docking.platform.backends.diagnostics import WindowReason, WindowTrackingDiagnostic
from docking.platform.environment import (
    backend_name,
    compositor_active,
    detect_desktop,
    is_wayland_session,
    is_x11_backend,
    is_xwayland_session,
)

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.backends.base import SessionBackend

CheckStatus = Literal["ok", "warning", "error", "info"]

ENVIRONMENT_KEYS: tuple[str, ...] = (
    "XDG_CURRENT_DESKTOP",
    "XDG_SESSION_DESKTOP",
    "XDG_SESSION_TYPE",
    "XDG_DATA_HOME",
    "XDG_DATA_DIRS",
    "DESKTOP_SESSION",
    "GDMSESSION",
    "DOCKING_BACKEND",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "GDK_BACKEND",
    "XDG_DESKTOP_PORTAL_DIR",
    "XDG_DESKTOP_PORTAL_DESKTOP",
    "DBUS_SESSION_BUS_ADDRESS",
    "FLATPAK_ID",
    "SNAP",
)

COMMAND_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("terminal-xdg", "x-terminal-emulator", "Run Command terminal fallback"),
    ("terminal-gnome", "gnome-terminal", "GNOME terminal launcher"),
    ("terminal-mate", "mate-terminal", "MATE terminal launcher"),
    ("terminal-xfce", "xfce4-terminal", "Xfce terminal launcher"),
    ("terminal-kde", "konsole", "KDE terminal launcher"),
    ("terminal-xterm", "xterm", "X11 terminal fallback"),
    ("portal-cli", "gdbus", "XDG desktop portal probing"),
    ("brightness", "brightnessctl", "Brightness applet"),
    ("audio-pulse", "pactl", "Volume and microphone applets"),
    ("audio-pipewire", "wpctl", "PipeWire volume control"),
    ("bluetooth", "bluetoothctl", "Bluetooth applet"),
    ("media", "playerctl", "Music applet fallback"),
    ("sensors", "sensors", "Thermals applet"),
    ("hyprland", "hyprctl", "Hyprland compositor integration"),
    ("kde-dbus", "qdbus", "KDE/KWin integration helper"),
    ("screenshot-gnome", "gnome-screenshot", "Screenshot applet fallback"),
    ("screenshot-mate", "mate-screenshot", "MATE screenshot helper"),
    ("screenshot-grim", "grim", "wlroots screenshot helper"),
    ("screenshot-slurp", "slurp", "wlroots region selection helper"),
)

IMPORT_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("gi", "gi", "Python GObject bindings"),
    ("dbus", "dbus", "DBus Python bindings"),
)


@dataclass(frozen=True)
class DiagnosticCheck:
    """One runtime compatibility fact."""

    id: str
    label: str
    status: CheckStatus
    detail: str
    fix_hint: str | None = None


@dataclass(frozen=True)
class DiagnosticFeature:
    """One user-facing feature derived from platform capabilities."""

    id: str
    label: str
    available: bool
    detail: str

    @property
    def status(self) -> CheckStatus:
        return "ok" if self.available else "warning"


@dataclass(frozen=True)
class MonitorDiagnostic:
    """Small monitor description shown in diagnostics."""

    index: int
    geometry: str
    scale: int
    primary: bool
    name: str | None = None
    connector: str | None = None
    workarea: str | None = None


@dataclass(frozen=True)
class DiagnosticsSnapshot:
    """Complete diagnostics payload for the UI and copied reports."""

    generated_at: datetime
    docking_version: str
    python_version: str
    gtk_version: str
    os_name: str
    desktop: str
    session_type: str
    gtk_backend: str
    display_server: DisplayServer
    backend_name: str
    backend_class: str
    forced_backend: str | None
    x11_backend: bool
    xwayland: bool
    wayland_session: bool
    compositor_active: bool | None
    environment: dict[str, str]
    features: tuple[DiagnosticFeature, ...]
    checks: tuple[DiagnosticCheck, ...]
    monitors: tuple[MonitorDiagnostic, ...]
    architecture: str = "unknown"
    kernel_version: str = "unknown"
    settings: dict[str, str] = field(default_factory=dict)
    window_tracking: WindowTrackingDiagnostic = field(
        default_factory=WindowTrackingDiagnostic
    )
    application_discovery: ApplicationDiscoveryDiagnostic | None = None

    @property
    def warnings(self) -> tuple[DiagnosticCheck, ...]:
        return tuple(
            check for check in self.checks if check.status in {"warning", "error"}
        )

    @property
    def health_label(self) -> str:
        errors = sum(1 for check in self.checks if check.status == "error")
        if errors:
            return "Problem detected"
        if self.backend_name == "reduced":
            return "Reduced compatibility"
        missing_core = any(
            not feature.available
            for feature in self.features
            if feature.id in {"running-indicators", "activate-windows", "edge-reserve"}
        )
        if missing_core:
            return "Mostly compatible"
        if any(check.status == "warning" for check in self.checks):
            return "Mostly compatible"
        return "No compatibility warnings detected"


def collect_diagnostics(
    *,
    backend: SessionBackend,
    display: object | None = None,
    config: Config | None = None,
    edge_gap: int | None = None,
    application_registry: ApplicationRegistry,
) -> DiagnosticsSnapshot:
    """Collect runtime diagnostics from the selected session backend."""
    capabilities = _backend_capabilities(backend)
    environment = _environment_snapshot()
    features = _feature_rows(capabilities)
    selected_backend = str(getattr(backend, "name", "") or _class_name(backend))
    display_server = _backend_display_server(backend)
    x11_backend = is_x11_backend(display=display)
    xwayland = is_xwayland_session(display=display)
    wayland_session = is_wayland_session()
    compositor = compositor_active(display=display)
    checks = _diagnostic_checks(
        capabilities=capabilities,
        backend_name=selected_backend,
        display_server=display_server,
        x11_backend=x11_backend,
        xwayland=xwayland,
        wayland_session=wayland_session,
        compositor_active_status=compositor,
    )
    try:
        tracking = backend.windows.diagnostic_snapshot()
    except Exception:
        tracking = WindowTrackingDiagnostic(
            status="failed", detail="Window diagnostic collection failed."
        )
    discovery = tracking.application_discovery
    if discovery is None:
        try:
            discovery = application_registry.diagnostic_snapshot()
        except Exception:
            discovery = None
    return DiagnosticsSnapshot(
        generated_at=datetime.now(tz=timezone.utc),
        docking_version=__version__,
        python_version=sys.version.split()[0],
        gtk_version=_gtk_version(),
        os_name=_os_name(),
        desktop=str(detect_desktop()),
        session_type=os.environ.get("XDG_SESSION_TYPE", "").strip() or "unknown",
        gtk_backend=backend_name(display=display),
        display_server=display_server,
        backend_name=selected_backend,
        backend_class=_class_name(backend),
        forced_backend=os.environ.get("DOCKING_BACKEND") or None,
        x11_backend=x11_backend,
        xwayland=xwayland,
        wayland_session=wayland_session,
        compositor_active=compositor,
        environment=environment,
        features=features,
        checks=checks,
        monitors=_monitor_rows(display=display),
        architecture=platform.machine(),
        kernel_version=platform.release(),
        settings=_settings_snapshot(config=config, edge_gap=edge_gap),
        window_tracking=tracking,
        application_discovery=discovery,
    )


def format_diagnostics_report(snapshot: DiagnosticsSnapshot) -> str:
    """Return a Markdown report suitable for GitHub issues."""
    lines = [
        "# Docking Diagnostics Report",
        "",
        f"Generated: {snapshot.generated_at.isoformat()}",
        "",
        "## Summary",
        "",
        f"- Health: {snapshot.health_label}",
        f"- Docking: {snapshot.docking_version}",
        f"- Python: {snapshot.python_version}",
        f"- GTK: {snapshot.gtk_version}",
        f"- OS: {snapshot.os_name}",
        f"- Architecture: {snapshot.architecture}",
        f"- Kernel: {snapshot.kernel_version}",
        f"- Desktop: {snapshot.desktop}",
        f"- Session: {snapshot.session_type}",
        f"- Selected backend: {snapshot.backend_name}",
        f"- Backend class: {snapshot.backend_class}",
        f"- Display server: {snapshot.display_server.value}",
        f"- GTK display backend: {snapshot.gtk_backend}",
        f"- Forced backend: {snapshot.forced_backend or 'none'}",
        f"- X11 GTK backend: {_yes_no(snapshot.x11_backend)}",
        f"- XWayland session: {_yes_no(snapshot.xwayland)}",
        f"- Wayland session: {_yes_no(snapshot.wayland_session)}",
        f"- X11 compositor active: {_unknown_yes_no(snapshot.compositor_active)}",
        "",
        "## Features",
        "",
        (
            "Features describe backend-reported support; "
            "actual behavior has not been verified."
        ),
        "",
    ]
    for feature in snapshot.features:
        lines.append(
            f"- [{'x' if feature.available else ' '}] {feature.label}: {feature.detail}"
        )
    lines.extend(["", "## Checks", ""])
    for check in snapshot.checks:
        lines.append(f"- {check.status.upper()}: {check.label} - {check.detail}")
        if check.fix_hint:
            lines.append(f"  Hint: {check.fix_hint}")
    lines.extend(["", "## Dock Settings", ""])
    if snapshot.settings:
        lines.extend(f"- {key}: {value}" for key, value in snapshot.settings.items())
    else:
        lines.append("- unavailable")
    lines.extend(["", "## Monitors", ""])
    if snapshot.monitors:
        for monitor in snapshot.monitors:
            primary = " primary" if monitor.primary else ""
            name = f" {monitor.name}" if monitor.name else ""
            connector = f" ({monitor.connector})" if monitor.connector else ""
            lines.append(
                f"- #{monitor.index}{primary}{name}{connector}: "
                f"{monitor.geometry}, scale {monitor.scale}"
            )
            lines.append(f"  GDK-reported workarea: {monitor.workarea or 'unknown'}")
    else:
        lines.append("- unavailable")
    lines.extend(_application_diagnostic_lines(snapshot))
    lines.extend(["", "## Environment", ""])
    for key, value in snapshot.environment.items():
        lines.append(f"- {key}: {_report_value(value)}")
    return "\n".join(lines).rstrip() + "\n"


def _report_value(value: object | None) -> str:
    """Escape a dynamic value as single-line Markdown text."""
    if value is None or value == "":
        return "unavailable"
    clean = "".join(char if char.isprintable() else " " for char in str(value))
    return re.sub(r"([\\`*_{}\[\]()<>|])", r"\\\1", clean)


def _application_diagnostic_lines(snapshot: DiagnosticsSnapshot) -> list[str]:
    lines = ["", "## Application Discovery", ""]
    discovery = snapshot.application_discovery
    if discovery is None:
        lines.append("- unavailable")
    else:
        status = "available" if discovery.loaded else "not yet discovered"
        lines.extend(
            [
                f"- Status: {status}",
                f"- Registry generation: {discovery.generation}",
                f"- Registered applications: {discovery.registered_count}",
                f"- Visible applications: {discovery.visible_count}",
                "- Inventory source: "
                + (
                    "registry snapshot retained by the window scan"
                    if snapshot.window_tracking.application_discovery is not None
                    else "current registry snapshot; not retained by a window scan"
                ),
                "- Directories used by last completed discovery (in order):",
            ]
        )
        lines.extend(f"  - {_report_value(path)}" for path in discovery.directories)
        if not discovery.directories:
            lines.append("  - unavailable")
        for directory in discovery.directory_statuses:
            lines.append(
                f"- Directory status: {_report_value(directory.path)}: "
                f"{directory.status.value}"
            )
    tracking = snapshot.window_tracking
    lines.extend(
        [
            "",
            "## Window Matching",
            "",
            (
                "Records describe the last tracking scan "
                "and may differ from current windows."
            ),
            "Window titles and full command lines are omitted.",
            "",
            f"- Status: {tracking.status}",
            f"- Detail: {_report_value(tracking.detail)}",
            (
                "- Registry generation at scan: "
                f"{_report_value(tracking.registry_generation)}"
            ),
        ]
    )
    if tracking.scanned_at is None:
        lines.append("- Last scan: unavailable")
    else:
        age = max(0, (snapshot.generated_at - tracking.scanned_at).total_seconds())
        lines.extend(
            [
                f"- Last scan: {tracking.scanned_at.isoformat()}",
                f"- Scan age: {age:.1f} seconds",
            ]
        )
    for outcome in ("matched", "unmatched", "excluded", "error"):
        count = sum(row.outcome == outcome for row in tracking.windows)
        lines.append(f"- {outcome.capitalize()}: {count}")
    for index, row in enumerate(tracking.windows, 1):
        reason = (
            "no matching application found"
            if row.reason is WindowReason.NO_MATCH
            else _report_value(row.reason.value)
        )
        lines.extend(
            [
                "",
                f"### Window {index}",
                "",
                f"- ID: {_report_value(row.window_id)}",
                f"- Outcome: {row.outcome}",
                f"- Reason: {reason}",
                f"- PID: {_report_value(row.pid)}",
                f"- Executable: {_report_value(row.executable_path)}",
                f"- Workspace: {_report_value(row.workspace)}",
            ]
        )
        for key, value in row.identities:
            lines.append(f"- {_report_value(key)}: {_report_value(value)}")
        for label, value in (
            ("Desktop ID", row.desktop_id),
            ("Match method", row.match_method),
            ("Matched identity", row.matched_identity),
            ("Desktop file", row.desktop_file),
            ("Launcher executable basename", row.launcher_basename),
            ("Process sandbox application ID", row.sandbox_app_id),
            ("Process Python script basename", row.script_basename),
        ):
            lines.append(f"- {label}: {_report_value(value)}")
        lines.append(f"- Registered aliases: {_report_value(', '.join(row.aliases))}")
        lines.append(
            f"- Failed property reads: {_report_value(', '.join(row.read_errors))}"
        )
        for hint in row.identity_hints:
            lines.append(
                f"- {_report_value(hint.property_name)}: {hint.status.value}"
                + (f"; value: {_report_value(hint.value)}" if hint.value else "")
            )
    lines.extend(_application_inventory_report(discovery))
    return lines


def _declared_class(value: str | None) -> str:
    """Distinguish unavailable declarations from an explicitly absent class."""
    return (
        "unavailable"
        if value is None
        else _report_value(value)
        if value
        else "not declared"
    )


def _application_inventory_report(
    discovery: ApplicationDiscoveryDiagnostic | None,
) -> list[str]:
    lines = ["", "## Application Inventory", "", "Full command lines are omitted."]
    if discovery is None or not discovery.loaded:
        lines.extend(["", "- unavailable"])
        return lines
    for app in discovery.applications:
        lines.extend(["", f"### {_report_value(app.desktop_id)}", ""])
        for label, value in (
            ("Name", app.name),
            ("Desktop file", app.desktop_file),
            ("Visible", "yes" if app.visible else "no"),
            ("Gio source", "yes" if app.has_gio_source else "no"),
            ("Effective matching class", app.wm_class),
            ("Launcher executable basename", app.launcher_basename),
            ("Registered aliases", ", ".join(app.aliases)),
            ("Flatpak application ID", app.flatpak_app_id),
        ):
            lines.append(f"- {label}: {_report_value(value)}")
        lines.append(
            f"- Gio StartupWMClass: {_declared_class(app.gio_startup_wm_class)}"
        )
        lines.append(
            "- Desktop-file StartupWMClass: "
            f"{_declared_class(app.file_startup_wm_class)}"
        )
    if not discovery.applications:
        lines.extend(["", "- none"])
    lines.extend(
        [
            "",
            "## Application Discovery Decisions",
            "",
            (
                "These source decisions do not change application matching. "
                "Unreadable file metadata may still have a registered Gio source."
            ),
        ]
    )
    for index, decision in enumerate(discovery.decisions, start=1):
        lines.extend(["", f"### Source {index}", ""])
        for label, value in (
            ("Desktop ID", decision.desktop_id),
            ("Source", decision.source),
            ("Reason", decision.reason.value),
            ("Desktop file", decision.desktop_file),
            ("Name", decision.name),
            ("Winning path", decision.winning_path),
            ("Launcher executable basename", decision.launcher_basename),
        ):
            lines.append(f"- {label}: {_report_value(value)}")
        lines.append(f"- StartupWMClass: {_declared_class(decision.startup_wm_class)}")
    if not discovery.decisions:
        lines.extend(["", "- none"])
    return lines


def _diagnostic_checks(
    *,
    capabilities: PlatformCapabilities,
    backend_name: str,
    display_server: DisplayServer,
    x11_backend: bool,
    xwayland: bool,
    wayland_session: bool,
    compositor_active_status: bool | None,
) -> tuple[DiagnosticCheck, ...]:
    checks: list[DiagnosticCheck] = []
    if backend_name == "reduced":
        checks.append(
            DiagnosticCheck(
                id="reduced-backend",
                label="Reduced backend",
                status="warning",
                detail="Docking is running with launcher-only platform integration.",
                fix_hint="Install or select a richer backend for this desktop session.",
            )
        )
    else:
        checks.append(
            DiagnosticCheck(
                id="selected-backend",
                label="Selected backend",
                status="ok",
                detail=f"{backend_name} on {display_server.value}",
            )
        )
    if xwayland:
        checks.append(
            DiagnosticCheck(
                id="xwayland",
                label="XWayland session",
                status="warning",
                detail="Docking is an X11 client inside a Wayland desktop.",
                fix_hint=(
                    "Native Wayland apps may have limited running indicators, "
                    "previews, and window actions unless a native backend is selected."
                ),
            )
        )
    elif wayland_session:
        checks.append(
            DiagnosticCheck(
                id="wayland",
                label="Wayland session",
                status="info",
                detail=(
                    "Compatibility depends on compositor protocols and backend support."
                ),
            )
        )
    if x11_backend:
        if compositor_active_status is False:
            checks.append(
                DiagnosticCheck(
                    id="x11-compositor",
                    label="X11 compositor",
                    status="warning",
                    detail="No active X11 compositor was detected.",
                    fix_hint=(
                        "Enable desktop compositing or run a compositor such as picom "
                        "so the dock renders with transparency instead of opaque."
                    ),
                )
            )
        elif compositor_active_status is True:
            checks.append(
                DiagnosticCheck(
                    id="x11-compositor",
                    label="X11 compositor",
                    status="ok",
                    detail="An X11 compositor is active.",
                )
            )
    if not capabilities.tracks_windows:
        checks.append(
            DiagnosticCheck(
                id="window-tracking",
                label="Window tracking",
                status="warning",
                detail="Running indicators and per-app window lists are unavailable.",
            )
        )
    if not capabilities.supports_screen_reservation:
        checks.append(
            DiagnosticCheck(
                id="screen-reservation",
                label="Screen reservation",
                status="warning",
                detail="The backend cannot reserve workspace space for the dock.",
            )
        )
    checks.extend(_dependency_checks())
    return tuple(checks)


def _dependency_checks() -> list[DiagnosticCheck]:
    checks: list[DiagnosticCheck] = []
    for check_id, module_name, label in IMPORT_CHECKS:
        available = importlib.util.find_spec(module_name) is not None
        checks.append(
            DiagnosticCheck(
                id=f"import-{check_id}",
                label=label,
                status="ok" if available else "warning",
                detail=(
                    f"Python module {module_name!r} is importable."
                    if available
                    else f"Python module {module_name!r} is not importable."
                ),
            )
        )
    for check_id, command, label in COMMAND_CHECKS:
        path = shutil.which(command)
        checks.append(
            DiagnosticCheck(
                id=f"command-{check_id}",
                label=label,
                status="ok" if path else "info",
                detail=path or f"{command} was not found in PATH.",
            )
        )
    return checks


def _feature_rows(capabilities: PlatformCapabilities) -> tuple[DiagnosticFeature, ...]:
    rows = (
        ("running-indicators", "Running indicators", capabilities.tracks_windows),
        ("active-window", "Active window state", capabilities.tracks_active_window),
        ("attention", "Attention badges", capabilities.tracks_attention),
        ("window-menu", "Window list menu", capabilities.supports_window_menu),
        ("activate-windows", "Activate windows", capabilities.supports_activate),
        ("minimize-windows", "Minimize windows", capabilities.supports_minimize),
        ("close-windows", "Close windows", capabilities.supports_close),
        ("window-geometry", "Window geometry", capabilities.tracks_window_geometry),
        (
            "window-outline",
            "Window outline on preview hover",
            capabilities.supports_window_outline,
        ),
        (
            "workspace-filter",
            "Current-workspace filtering",
            capabilities.supports_current_workspace_filter,
        ),
        ("workspace-list", "Workspace list", capabilities.supports_workspace_list),
        (
            "workspace-switch",
            "Workspace switching",
            capabilities.supports_workspace_switch,
        ),
        ("layer-shell", "Wayland layer-shell", capabilities.supports_layer_shell),
        (
            "edge-reserve",
            "Screen edge reservation",
            capabilities.supports_screen_reservation,
        ),
        ("input-region", "Input region shaping", capabilities.supports_input_region),
        (
            "pointer-barrier",
            "Pointer pressure reveal",
            capabilities.supports_pointer_barrier,
        ),
        (
            "blur-region",
            "Background blur hint",
            capabilities.supports_background_blur_hint,
        ),
        (
            "dodge",
            "Window dodge / overlap detection",
            capabilities.supports_any_overlap,
        ),
        (
            "color-picker",
            "Screen color picker",
            capabilities.supports_screen_color_pick,
        ),
        ("idle-time", "Idle time", capabilities.supports_idle_time),
        (
            "window-killer",
            "Window picker / process kill",
            _supports_window_killer(capabilities),
        ),
    )
    return tuple(
        DiagnosticFeature(
            id=feature_id,
            label=label,
            available=available,
            detail=(
                "supported by selected backend"
                if available
                else "unsupported by selected backend"
            ),
        )
        for feature_id, label, available in rows
    )


def _supports_window_killer(capabilities: PlatformCapabilities) -> bool:
    return capabilities.supports_window_pick and capabilities.supports_process_kill


def _monitor_rows(*, display: object | None) -> tuple[MonitorDiagnostic, ...]:
    if display is None:
        return ()
    try:
        count = int(display.get_n_monitors())
    except Exception:
        return ()
    rows: list[MonitorDiagnostic] = []
    for index in range(count):
        try:
            monitor = display.get_monitor(index)
            geometry = monitor.get_geometry()
            connector = _optional_monitor_detail(monitor, "get_connector")
            workarea = _optional_monitor_detail(monitor, "get_workarea")
            rows.append(
                MonitorDiagnostic(
                    index=index,
                    geometry=(
                        f"{geometry.x},{geometry.y} {geometry.width}x{geometry.height}"
                    ),
                    scale=int(monitor.get_scale_factor()),
                    primary=monitor == display.get_primary_monitor(),
                    name=monitor.get_model() or None,
                    connector=str(connector) if connector else None,
                    workarea=(
                        f"{workarea.x},{workarea.y} {workarea.width}x{workarea.height}"
                        if workarea is not None
                        else None
                    ),
                )
            )
        except Exception:
            continue
    return tuple(rows)


def _optional_monitor_detail(monitor: object, method: str) -> object | None:
    try:
        getter = getattr(monitor, method, None)
        return getter() if callable(getter) else None
    except Exception:
        return None


def _settings_snapshot(
    *, config: Config | None, edge_gap: int | None
) -> dict[str, str]:
    """Include placement and workspace scope, never private preferences."""
    if config is None:
        return {}
    return {
        "position": config.position,
        "hide_mode": config.hide_mode,
        "monitor_index": str(config.monitor_index),
        "monitor_connector": config.monitor_connector or "none",
        "active_display": _yes_no(config.active_display),
        "current_workspace_only": _yes_no(config.current_workspace_only),
        "icon_size": f"{config.icon_size} px",
        "zoom_enabled": _yes_no(config.zoom_enabled),
        "zoom_multiplier": f"{config.zoom_percent:g}",
        "effective_edge_gap": f"{edge_gap} px" if edge_gap is not None else "unknown",
    }


def _backend_capabilities(backend: object) -> PlatformCapabilities:
    capabilities = getattr(backend, "capabilities", None)
    if isinstance(capabilities, PlatformCapabilities):
        return capabilities
    return PlatformCapabilities()


def _backend_display_server(backend: object) -> DisplayServer:
    display_server = getattr(backend, "display_server", None)
    if isinstance(display_server, DisplayServer):
        return display_server
    return DisplayServer.NONE


def _environment_snapshot() -> dict[str, str]:
    return {
        key: _redact_env_value(key, os.environ.get(key, ""))
        for key in ENVIRONMENT_KEYS
        if os.environ.get(key) is not None
    }


def _redact_env_value(key: str, value: str) -> str:
    if key == "DBUS_SESSION_BUS_ADDRESS" and value:
        return "<set>"
    return value


def _gtk_version() -> str:
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        return (
            f"{Gtk.get_major_version()}."
            f"{Gtk.get_minor_version()}."
            f"{Gtk.get_micro_version()}"
        )
    except Exception:
        return "unknown"


def _os_name() -> str:
    try:
        data = platform.freedesktop_os_release()
    except OSError:
        data = {}
    pretty = data.get("PRETTY_NAME")
    if pretty:
        return pretty
    return platform.platform()


def _class_name(value: object) -> str:
    cls = value.__class__
    return f"{cls.__module__}.{cls.__name__}"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _unknown_yes_no(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return _yes_no(value)
