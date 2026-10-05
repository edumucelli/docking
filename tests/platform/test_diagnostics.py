from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from docking.core.config import Config
from docking.platform.applications.registry import ApplicationRegistry
from docking.platform.applications.types import ApplicationDiscoveryDiagnostic
from docking.platform.backends.base import DisplayServer, PlatformCapabilities
from docking.platform.backends.diagnostics import (
    WindowDiagnostic,
    WindowReason,
    WindowTrackingDiagnostic,
)
from docking.platform.diagnostics import collect_diagnostics, format_diagnostics_report


class _Backend:
    name = "test-backend"
    display_server = DisplayServer.X11
    windows = SimpleNamespace(diagnostic_snapshot=WindowTrackingDiagnostic)
    capabilities = PlatformCapabilities(
        tracks_windows=True,
        tracks_active_window=True,
        supports_activate=True,
        supports_screen_reservation=True,
        supports_input_region=True,
    )


class _ReducedBackend:
    name = "reduced"
    display_server = DisplayServer.NONE
    capabilities = PlatformCapabilities()
    windows = SimpleNamespace(diagnostic_snapshot=WindowTrackingDiagnostic)


def test_window_report_uses_cached_evidence_and_escapes_dynamic_values(monkeypatch):
    from datetime import datetime, timezone
    from pathlib import Path
    from unittest.mock import MagicMock

    stamp = datetime(2026, 10, 5, tzinfo=timezone.utc)
    tracking = WindowTrackingDiagnostic(
        status="available",
        scanned_at=stamp,
        registry_generation=7,
        windows=(
            WindowDiagnostic(
                window_id="x11:42",
                identities=(("wm-class", "bad`[id]\n# injected"),),
                pid=42,
                executable_path="/opt/app",
                workspace="2",
                reason=WindowReason.NO_MATCH,
            ),
            WindowDiagnostic(
                window_id="x11:43",
                outcome="matched",
                reason=WindowReason.INCLUDED,
                desktop_id="app.desktop",
                match_method="wm-class",
                desktop_file="/applications/app.desktop",
                launcher_basename="app",
                aliases=("app",),
            ),
        ),
    )
    windows = SimpleNamespace(diagnostic_snapshot=MagicMock(return_value=tracking))
    backend = _Backend()
    backend.windows = windows
    registry = ApplicationRegistry()
    registry_snapshot = ApplicationDiscoveryDiagnostic(
        generation=8,
        registered_count=2,
        visible_count=1,
        directories=(Path("/applications"),),
        loaded=True,
    )
    monkeypatch.setattr(registry, "diagnostic_snapshot", lambda: registry_snapshot)
    monkeypatch.setattr(registry, "refresh", lambda: pytest.fail("No rediscovery"))
    snapshot = collect_diagnostics(backend=backend, application_registry=registry)
    snapshot = replace(snapshot, generated_at=stamp)
    report = format_diagnostics_report(snapshot)
    assert snapshot.window_tracking is tracking
    assert "- Registry generation at scan: 7" in report
    assert "- Registry generation: 8" in report
    assert "- Matched: 1" in report and "- Unmatched: 1" in report
    assert "- Scan age: 0.0 seconds" in report
    assert "no matching application found" in report
    assert "bad\\`\\[id\\] # injected" in report
    assert "\n# injected" not in report
    assert "/applications/app.desktop" in report
    windows.diagnostic_snapshot.assert_called_once_with()


def test_diagnostic_failures_do_not_break_report(monkeypatch):
    def fail():
        raise RuntimeError("PRIVATE-DETAIL")

    backend = _Backend()
    backend.windows = SimpleNamespace(diagnostic_snapshot=fail)
    registry = ApplicationRegistry()
    monkeypatch.setattr(registry, "diagnostic_snapshot", fail)
    snapshot = collect_diagnostics(backend=backend, application_registry=registry)
    report = format_diagnostics_report(snapshot)
    assert snapshot.window_tracking.status == "failed"
    assert "## Features" in report
    assert "## Application Discovery\n\n- unavailable" in report
    assert "PRIVATE-DETAIL" not in report


def test_report_uses_scan_inventory_after_registry_changes(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from unittest.mock import MagicMock

    from docking.platform.backends.diagnostics import (
        IdentityHintStatus,
        WindowIdentityHint,
    )

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "app.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Bad `[name]\n"
        "Exec=flatpak run org.example.App --token=PRIVATE\n"
    )
    (second / "app.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Overridden\nExec=lower PRIVATE\n"
    )
    registry = ApplicationRegistry(
        application_source=lambda: (),
        desktop_directories_source=lambda: (first, second),
    )
    registry._desktop_app_info_for_id = lambda _id: None
    registry._desktop_app_info_from_filename = lambda _path: None
    registry.refresh()
    retained = registry.diagnostic_snapshot()
    tracking = WindowTrackingDiagnostic(
        status="available",
        scanned_at=datetime.now(tz=timezone.utc),
        registry_generation=retained.generation,
        application_discovery=retained,
        windows=(
            WindowDiagnostic(
                window_id="x11:1",
                identity_hints=(
                    WindowIdentityHint(
                        "_GTK_APPLICATION_ID",
                        IdentityHintStatus.PRESENT,
                        "bad`[hint]\n# injected",
                    ),
                    WindowIdentityHint(
                        "_KDE_NET_WM_DESKTOP_FILE", IdentityHintStatus.ABSENT
                    ),
                ),
            ),
        ),
    )
    (first / "app.desktop").unlink()
    (second / "app.desktop").unlink()
    registry.refresh()
    assert registry.diagnostic_snapshot().registered_count == 0
    monkeypatch.setattr(
        registry, "diagnostic_snapshot", lambda: pytest.fail("Use retained evidence")
    )
    windows = SimpleNamespace(diagnostic_snapshot=MagicMock(return_value=tracking))
    backend = _Backend()
    backend.windows = windows
    snapshot = collect_diagnostics(backend=backend, application_registry=registry)
    report = format_diagnostics_report(snapshot)
    assert snapshot.application_discovery is retained
    assert "registry snapshot retained by the window scan" in report
    assert "## Application Inventory" in report
    assert "- Name: Bad \\`\\[name\\]" in report
    assert "- Gio StartupWMClass: unavailable" in report
    assert "- Desktop-file StartupWMClass: not declared" in report
    assert "- Effective matching class: flatpak" in report
    assert "- Reason: shadowed-source" in report
    assert str(first / "app.desktop").replace("_", "\\_") in report
    assert r"- \_KDE\_NET\_WM\_DESKTOP\_FILE: absent" in report
    assert "bad\\`\\[hint\\] # injected" in report
    assert "\n# injected" not in report
    assert "PRIVATE" not in report


def test_cinnamon_shell_reports_pending_tracking_without_querying_shell():
    from unittest.mock import MagicMock

    from docking.platform.backends.cinnamon.session import CinnamonShellSessionBackend
    from tests.platform.application_fakes import identity_services

    client = SimpleNamespace(list_windows=MagicMock(), features=frozenset())
    backend = CinnamonShellSessionBackend(
        client=client, model=MagicMock(), **identity_services()
    )
    snapshot = collect_diagnostics(
        backend=backend, application_registry=ApplicationRegistry()
    )
    assert snapshot.window_tracking.status == "pending"
    assert backend.capabilities.tracks_windows
    assert backend.capabilities.supports_activate
    client.list_windows.assert_not_called()


def test_match_diagnostics_omit_full_launcher_arguments():
    from docking.platform.applications.types import (
        ApplicationMatch,
        MatchEvidence,
        MatchMethod,
    )
    from docking.platform.backends.diagnostics import with_match
    from tests.platform.application_fakes import application

    app = replace(application("app.desktop"), exec_line="/opt/app PRIVATE-ARGUMENT")
    record = with_match(
        WindowDiagnostic(),
        ApplicationMatch(
            desktop_id=app.desktop_id,
            application=app,
            evidence=MatchEvidence(method=MatchMethod.WM_CLASS, raw_app_id="app"),
        ),
    )
    assert record.launcher_basename == "app"
    assert record.desktop_id == "app.desktop"
    assert "PRIVATE-ARGUMENT" not in repr(record)


def test_collect_diagnostics_reports_backend_and_features(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DOCKING_BACKEND", "test-backend")

    snapshot = collect_diagnostics(
        application_registry=ApplicationRegistry(), backend=_Backend(), display=None
    )

    assert snapshot.backend_name == "test-backend"
    assert snapshot.display_server is DisplayServer.X11
    assert snapshot.forced_backend == "test-backend"
    features = {feature.id: feature.available for feature in snapshot.features}
    assert features["running-indicators"] is True
    assert features["activate-windows"] is True
    assert features["edge-reserve"] is True
    assert features["workspace-list"] is False


def test_reduced_backend_gets_reduced_health(monkeypatch):
    monkeypatch.delenv("DOCKING_BACKEND", raising=False)

    snapshot = collect_diagnostics(
        application_registry=ApplicationRegistry(),
        backend=_ReducedBackend(),
        display=None,
    )

    assert snapshot.health_label == "Reduced compatibility"
    assert any(check.id == "reduced-backend" for check in snapshot.checks)


def test_report_contains_issue_relevant_runtime_fields(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")

    snapshot = collect_diagnostics(
        application_registry=ApplicationRegistry(), backend=_Backend(), display=None
    )
    report = format_diagnostics_report(snapshot)

    assert "# Docking Diagnostics Report" in report
    assert "- Selected backend: test-backend" in report
    assert "- Display server: x11" in report
    assert "## Features" in report
    assert "## Checks" in report
    assert "WAYLAND_DISPLAY" in report


def test_report_includes_settings_and_system_details_without_private_config(
    monkeypatch,
):
    monkeypatch.setattr(
        "docking.platform.diagnostics.platform.machine", lambda: "aarch64"
    )
    monkeypatch.setattr(
        "docking.platform.diagnostics.platform.release", lambda: "6.12-test"
    )
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=PRIVATE-BUS-ADDRESS")
    config = Config(
        position="left",
        hide_mode="none",
        monitor_index=1,
        monitor_connector="DP-2",
        active_display=True,
        icon_size=64,
        zoom_enabled=False,
        zoom_percent=1.75,
        item_prefs={"applet://mail": {"password": "PRIVATE-APPLET-PASSWORD"}},
        theme="PRIVATE-UNRELATED-SETTING",
    )
    monkeypatch.setattr(
        Config, "to_dict", lambda self: pytest.fail("Do not dump config")
    )

    snapshot = collect_diagnostics(
        application_registry=ApplicationRegistry(),
        backend=_Backend(),
        config=config,
        edge_gap=12,
    )
    report = format_diagnostics_report(snapshot)

    assert snapshot.settings == {
        "position": "left",
        "hide_mode": "none",
        "monitor_index": "1",
        "monitor_connector": "DP-2",
        "active_display": "yes",
        "current_workspace_only": "no",
        "icon_size": "64 px",
        "zoom_enabled": "no",
        "zoom_multiplier": "1.75",
        "effective_edge_gap": "12 px",
    }
    assert "- Architecture: aarch64" in report
    assert "- Kernel: 6.12-test" in report
    assert "## Dock Settings" in report
    assert "- effective_edge_gap: 12 px" in report
    assert "PRIVATE-" not in report


def test_settings_are_optional_and_missing_effective_gap_is_unknown():
    snapshot = collect_diagnostics(
        application_registry=ApplicationRegistry(), backend=_Backend()
    )
    assert snapshot.settings == {}
    assert "## Dock Settings\n\n- unavailable" in format_diagnostics_report(snapshot)

    snapshot = collect_diagnostics(
        application_registry=ApplicationRegistry(), backend=_Backend(), config=Config()
    )
    assert snapshot.settings["effective_edge_gap"] == "unknown"


@pytest.mark.parametrize("optional_details", ["available", "missing", "failing"])
def test_monitors_keep_geometry_when_optional_details_are_unavailable(optional_details):
    monitor = SimpleNamespace(
        get_geometry=lambda: SimpleNamespace(x=-1920, y=0, width=1920, height=1080),
        get_scale_factor=lambda: 2,
        get_model=lambda: "Test monitor",
    )
    if optional_details == "available":
        monitor.get_connector = lambda: "DP-2"
        monitor.get_workarea = lambda: SimpleNamespace(
            x=-1920, y=40, width=1920, height=1040
        )
    elif optional_details == "failing":

        def unavailable():
            raise RuntimeError("Monitor detail unavailable")

        monitor.get_connector = unavailable
        monitor.get_workarea = unavailable
    display = SimpleNamespace(
        get_n_monitors=lambda: 1,
        get_monitor=lambda index: monitor,
        get_primary_monitor=lambda: monitor,
    )

    snapshot = collect_diagnostics(
        application_registry=ApplicationRegistry(), backend=_Backend(), display=display
    )
    (row,) = snapshot.monitors
    assert row.geometry == "-1920,0 1920x1080"
    assert row.scale == 2
    assert row.primary is True
    report = format_diagnostics_report(snapshot)
    if optional_details == "available":
        assert row.connector == "DP-2"
        assert row.workarea == "-1920,40 1920x1040"
        assert "Test monitor (DP-2)" in report
        assert "GDK-reported workarea: -1920,40 1920x1040" in report
    else:
        assert row.connector is None
        assert row.workarea is None
        assert "GDK-reported workarea: unknown" in report


def test_supported_capabilities_do_not_claim_verified_runtime_behavior():
    snapshot = replace(
        collect_diagnostics(
            application_registry=ApplicationRegistry(), backend=_Backend()
        ),
        checks=(),
    )
    assert snapshot.health_label == "No compatibility warnings detected"
    assert "actual behavior has not been verified" in format_diagnostics_report(
        snapshot
    )
    assert all("available" not in feature.detail for feature in snapshot.features)
