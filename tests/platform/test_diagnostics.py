from __future__ import annotations

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
