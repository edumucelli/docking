"""Tests for Cinnamon Wayland's read-only Muffin integration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.core.items import DockItem
from docking.platform.applications.types import (
    ApplicationMatch,
    ApplicationMatchAttempt,
    MatchEvidence,
    MatchMethod,
)
from docking.platform.backends.base import ActionResult
from docking.platform.backends.cinnamon.muffin import MuffinWindowService
from docking.platform.backends.cinnamon.session import CinnamonWaylandSessionBackend
from docking.platform.backends.diagnostics import WindowReason
from tests.platform.application_fakes import identity_services


def _model() -> SimpleNamespace:
    return SimpleNamespace(
        visible_items=MagicMock(
            return_value=[DockItem(desktop_id="firefox.desktop", wm_class="firefox")]
        ),
        update_running=MagicMock(),
    )


def _layer_shell() -> SimpleNamespace:
    return SimpleNamespace(
        Edge=SimpleNamespace(TOP=1, BOTTOM=2, LEFT=4, RIGHT=8),
        Layer=SimpleNamespace(TOP=1),
        KeyboardMode=SimpleNamespace(NONE=0),
        init_for_window=MagicMock(),
        set_namespace=MagicMock(),
        set_layer=MagicMock(),
        set_keyboard_mode=MagicMock(),
        set_anchor=MagicMock(),
        set_margin=MagicMock(),
        set_monitor=MagicMock(),
        set_size=MagicMock(),
        set_exclusive_zone=MagicMock(),
    )


def test_muffin_diagnostics_capture_all_rows_and_lifecycle():
    model = _model()
    client = SimpleNamespace(
        list_windows=MagicMock(
            return_value=(
                {
                    "id": 1,
                    "title": "PRIVATE-DOCUMENT",
                    "app-id": "unknown",
                    "sandboxed-app-id": "firefox",
                    "pid": 0,
                },
                {"id": 2, "wm-class": "unknown"},
                {"id": 3, "wm-class": "firefox", "skip-taskbar": True},
                {"id": 4},
                {"id": "invalid"},
            )
        ),
        last_query_failed=False,
    )
    service = MuffinWindowService(model=model, client=client, **identity_services())
    assert service.diagnostic_snapshot().status == "pending"
    service.refresh()
    snapshot = service.diagnostic_snapshot()
    assert snapshot.status == "available"
    assert [row.outcome for row in snapshot.windows] == [
        "matched",
        "unmatched",
        "excluded",
        "unmatched",
        "error",
    ]
    assert snapshot.windows[0].matched_identity == "firefox"
    assert snapshot.windows[0].identities == (
        ("app-id", "unknown"),
        ("sandboxed-app-id", "firefox"),
    )
    assert snapshot.windows[3].reason is WindowReason.NO_IDENTITY
    assert snapshot.windows[4].reason is WindowReason.INVALID_WINDOW_ID
    assert "PRIVATE-DOCUMENT" not in repr(snapshot)
    assert (
        model.update_running.call_args.kwargs["running"]["firefox.desktop"].count == 1
    )
    client.list_windows.reset_mock()
    assert service.diagnostic_snapshot() is snapshot
    client.list_windows.assert_not_called()
    client.list_windows.return_value = ()
    client.last_query_failed = True
    service.refresh()
    assert service.diagnostic_snapshot().status == "failed"
    client.last_query_failed = False
    service.refresh()
    assert service.diagnostic_snapshot().status == "available"
    assert service.diagnostic_snapshot().windows == ()
    service.stop()
    assert service.diagnostic_snapshot().status == "stopped"
    assert service.diagnostic_snapshot().windows == ()


def test_muffin_client_distinguishes_query_failure_from_empty_results():
    from docking.platform.backends.cinnamon.muffin import MuffinDebugClient

    proxy = MagicMock()
    client = MuffinDebugClient(proxy=proxy)
    proxy.call_sync.side_effect = RuntimeError("PRIVATE-ERROR")
    assert client.list_windows() == ()
    assert client.last_query_failed
    proxy.call_sync.side_effect = None
    proxy.call_sync.return_value.unpack.return_value = ([],)
    assert client.list_windows() == ()
    assert not client.last_query_failed


def test_muffin_payload_flows_through_real_matcher_to_model():
    model = _model()
    client = SimpleNamespace(
        last_query_failed=False,
        list_windows=MagicMock(
            return_value=(
                {
                    "id": 42,
                    "title": "Browser",
                    "app-id": "firefox",
                    "focused": True,
                    "demands-attention": True,
                    "workspace": 2,
                    "frame-rect": (10, 20, 800, 600),
                    "skip-taskbar": False,
                },
            )
        ),
    )
    services = identity_services()
    service = MuffinWindowService(
        model=model,
        **services,
        client=client,
    )

    service.refresh()

    application_match = service._windows[42].application_match
    assert application_match is not None
    assert application_match.desktop_id == "firefox.desktop"
    assert application_match.application is services["application_registry"].get(
        "firefox.desktop"
    )
    snapshot = service.list_windows("firefox.desktop")[0]
    assert snapshot.active is True
    assert snapshot.urgent is True
    assert snapshot.geometry is not None
    assert snapshot.geometry.width == 800
    assert snapshot.workspace_id == "2"
    assert snapshot.can_activate is False
    assert service.activate(snapshot.id) is ActionResult.UNSUPPORTED
    running = model.update_running.call_args.kwargs["running"]
    assert running["firefox.desktop"].active is True
    assert running["firefox.desktop"].urgent is True


def test_muffin_window_service_uses_snapshot_identity_fallbacks(monkeypatch):
    model = _model()
    client = SimpleNamespace(
        last_query_failed=False,
        list_windows=MagicMock(
            return_value=(
                {
                    "id": 42,
                    "title": "Browser",
                    "app-id": "",
                    "gtk-application-id": "",
                    "sandboxed-app-id": "",
                    "wm-class": "Firefox",
                    "skip-taskbar": False,
                },
            )
        ),
    )
    service = MuffinWindowService(
        model=model,
        **identity_services(),
        client=client,
    )
    application_match = ApplicationMatch(
        desktop_id="firefox.desktop",
        application=None,
        evidence=MatchEvidence(
            method=MatchMethod.WM_CLASS,
            raw_app_id="Firefox",
        ),
    )
    match_result = MagicMock(
        return_value=ApplicationMatchAttempt(match=application_match)
    )
    monkeypatch.setattr(service._matcher, "match_attempt", match_result)

    service.refresh()

    match_result.assert_called_once_with("Firefox", process_id=None)
    assert service._windows[42].application_match is application_match
    snapshot = service.list_windows("firefox.desktop")[0]
    assert snapshot.app_id == "Firefox"


def test_cinnamon_session_advertises_only_available_window_capabilities(monkeypatch):
    monkeypatch.setattr(
        "docking.platform.backends.wayland.session.WaylandProtocolRuntime.start",
        lambda _runtime: False,
    )
    backend = CinnamonWaylandSessionBackend(
        layer_shell=_layer_shell(),
        model=_model(),
        **identity_services(),
        client=SimpleNamespace(
            last_query_failed=False, list_windows=MagicMock(return_value=())
        ),
    )

    assert backend.name == "cinnamon-wayland"
    assert backend.capabilities.tracks_windows is True
    assert backend.capabilities.tracks_attention is True
    assert backend.capabilities.tracks_window_geometry is True
    assert backend.capabilities.supports_activate is False
    assert backend.capabilities.supports_close is False
