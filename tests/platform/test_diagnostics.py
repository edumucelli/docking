from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from docking.core.config import Config
from docking.platform.backends.base import DisplayServer, PlatformCapabilities
from docking.platform.diagnostics import collect_diagnostics, format_diagnostics_report


class _Backend:
    name = "test-backend"
    display_server = DisplayServer.X11
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


def test_collect_diagnostics_reports_backend_and_features(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DOCKING_BACKEND", "test-backend")

    snapshot = collect_diagnostics(backend=_Backend(), display=None)

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

    snapshot = collect_diagnostics(backend=_ReducedBackend(), display=None)

    assert snapshot.health_label == "Reduced compatibility"
    assert any(check.id == "reduced-backend" for check in snapshot.checks)


def test_report_contains_issue_relevant_runtime_fields(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")

    snapshot = collect_diagnostics(backend=_Backend(), display=None)
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

    snapshot = collect_diagnostics(backend=_Backend(), config=config, edge_gap=12)
    report = format_diagnostics_report(snapshot)

    assert snapshot.settings == {
        "position": "left",
        "hide_mode": "none",
        "monitor_index": "1",
        "monitor_connector": "DP-2",
        "active_display": "yes",
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
    snapshot = collect_diagnostics(backend=_Backend())
    assert snapshot.settings == {}
    assert "## Dock Settings\n\n- unavailable" in format_diagnostics_report(snapshot)

    snapshot = collect_diagnostics(backend=_Backend(), config=Config())
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

    snapshot = collect_diagnostics(backend=_Backend(), display=display)
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
    snapshot = replace(collect_diagnostics(backend=_Backend()), checks=())
    assert snapshot.health_label == "No compatibility warnings detected"
    assert "actual behavior has not been verified" in format_diagnostics_report(
        snapshot
    )
    assert all("available" not in feature.detail for feature in snapshot.features)
