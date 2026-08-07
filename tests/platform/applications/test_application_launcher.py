"""Tests for registry-backed selected-application launching."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.platform.applications.launcher as launcher_mod
from docking.platform.applications.launcher import ApplicationLauncher
from docking.platform.applications.types import (
    ActionSource,
    ApplicationAction,
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
)


class _Registry:
    def __init__(self, applications: list[ApplicationInfo]) -> None:
        self.applications = {
            application.desktop_id: application for application in applications
        }
        self.handles: dict[str, object] = {}
        self.listing_handles: dict[str, object] = {}

    def resolve(
        self,
        desktop_id: str,
        *,
        log_failures: bool = True,
    ) -> ApplicationInfo | None:
        _ = log_failures
        return self.applications.get(desktop_id)

    def _gio_handle_for(self, desktop_id: str) -> object | None:
        return self.handles.get(desktop_id)

    def _gio_handle_for_unidentified(self, listing_key: str) -> object | None:
        return self.listing_handles.get(listing_key)


def _application(
    *,
    desktop_id: str = "example.desktop",
    exec_line: str = "/opt/example/bin/example %U",
    location: ApplicationLocation = ApplicationLocation.SANDBOX,
    has_gio_source: bool = True,
    actions: tuple[ApplicationAction, ...] = (),
) -> ApplicationInfo:
    return ApplicationInfo(
        desktop_id=desktop_id,
        name="Example",
        declared_icon="example",
        wm_class="Example",
        exec_line=exec_line,
        origin=ApplicationOrigin.INSTALLED,
        location=location,
        desktop_file=Path(f"/applications/{desktop_id}"),
        executable_path=Path("/opt/example/bin/example"),
        aliases=("example",),
        visible=True,
        has_gio_source=has_gio_source,
        actions=actions,
    )


def _launcher(
    application: ApplicationInfo,
    *,
    popen=None,
) -> tuple[ApplicationLauncher, _Registry, MagicMock]:
    registry = _Registry([application])
    store = MagicMock()
    return (
        ApplicationLauncher(
            registry,  # type: ignore[arg-type]
            store,
            popen=popen,
        ),
        registry,
        store,
    )


def test_quicklist_uses_gio_only_when_application_is_gio_backed():
    actions = (
        ApplicationAction(
            action_id="shared",
            name="Shared",
            sources=frozenset({ActionSource.GIO, ActionSource.DESKTOP_FILE}),
        ),
        ApplicationAction(
            action_id="file-only",
            name="File Only",
            sources=frozenset({ActionSource.DESKTOP_FILE}),
        ),
    )
    gio_launcher, _registry, _store = _launcher(_application(actions=actions))
    file_launcher, _registry, _store = _launcher(
        _application(has_gio_source=False, actions=actions)
    )

    assert gio_launcher.get_actions("example.desktop") == [("shared", "Shared")]
    assert file_launcher.get_actions("example.desktop") == [
        ("shared", "Shared"),
        ("file-only", "File Only"),
    ]


def test_direct_launch_strips_field_codes_sets_flags_and_records_provenance():
    process = SimpleNamespace(pid=123, poll=lambda: None)
    popen = MagicMock(return_value=process)
    launcher, _registry, store = _launcher(
        _application(exec_line='"/opt/example/bin/example" --new-window "%u"'),
        popen=popen,
    )

    assert launcher.launch("example.desktop") is True

    popen.assert_called_once()
    assert popen.call_args.args[0] == [
        "/opt/example/bin/example",
        "--new-window",
    ]
    assert popen.call_args.kwargs["shell"] is False
    assert popen.call_args.kwargs["start_new_session"] is True
    store.record_launch.assert_called_once_with(
        process=process,
        desktop_id="example.desktop",
        executable_path=Path("/opt/example/bin/example"),
    )


def test_host_launch_routes_through_flatpak_spawn(monkeypatch):
    popen = MagicMock(return_value=SimpleNamespace(pid=123, poll=lambda: None))
    launcher, _registry, _store = _launcher(
        _application(location=ApplicationLocation.HOST),
        popen=popen,
    )
    monkeypatch.setattr(
        launcher_mod.flatpak,
        "host_command",
        lambda argv: ["flatpak-spawn", "--host", *argv],
    )

    assert launcher.launch("example.desktop") is True
    assert popen.call_args.args[0] == [
        "flatpak-spawn",
        "--host",
        "/opt/example/bin/example",
    ]


def test_gio_action_and_uri_launches_do_not_record_provenance():
    action = ApplicationAction(
        action_id="new-window",
        name="New Window",
        sources=frozenset({ActionSource.GIO}),
    )
    launcher, registry, store = _launcher(_application(actions=(action,)))
    handle = MagicMock()
    registry.handles["example.desktop"] = handle

    assert launcher.launch_action("example.desktop", "new-window") is True
    assert launcher.launch_app_uris(
        "example.desktop",
        ["file:///tmp/one", "file:///tmp/two"],
    )

    handle.launch_action.assert_called_once_with("new-window", None)
    handle.launch_uris.assert_called_once_with(
        ["file:///tmp/one", "file:///tmp/two"],
        None,
    )
    store.record_launch.assert_not_called()


def test_gio_backed_file_only_merged_action_still_routes_through_gio():
    action = ApplicationAction(
        action_id="private",
        name="Private",
        sources=frozenset({ActionSource.DESKTOP_FILE}),
        file_exec_line="/opt/example/bin/example --private %U",
    )
    popen = MagicMock()
    launcher, registry, store = _launcher(
        _application(actions=(action,)),
        popen=popen,
    )
    handle = MagicMock()
    registry.handles["example.desktop"] = handle

    assert launcher.get_actions("example.desktop") == []
    assert launcher.launch_action("example.desktop", "private") is True

    handle.launch_action.assert_called_once_with("private", None)
    popen.assert_not_called()
    store.record_launch.assert_not_called()


def test_host_file_only_action_uses_its_exec_fallback(monkeypatch):
    action = ApplicationAction(
        action_id="private",
        name="Private",
        sources=frozenset({ActionSource.DESKTOP_FILE}),
        file_exec_line="/opt/example/bin/example --private %U",
    )
    process = SimpleNamespace(pid=456, poll=lambda: None)
    popen = MagicMock(return_value=process)
    launcher, registry, store = _launcher(
        _application(location=ApplicationLocation.HOST, actions=(action,)),
        popen=popen,
    )
    handle = MagicMock()
    registry.handles["example.desktop"] = handle
    monkeypatch.setattr(
        launcher_mod.flatpak,
        "host_command",
        lambda argv: ["flatpak-spawn", "--host", *argv],
    )

    assert launcher.launch_action("example.desktop", "private") is True

    assert popen.call_args.args[0] == [
        "flatpak-spawn",
        "--host",
        "/opt/example/bin/example",
        "--private",
    ]
    handle.launch_action.assert_not_called()
    store.record_launch.assert_called_once()


def test_non_gio_file_only_action_uses_its_exec_fallback():
    action = ApplicationAction(
        action_id="private",
        name="Private",
        sources=frozenset({ActionSource.DESKTOP_FILE}),
        file_exec_line="/opt/example/bin/example --private %U",
    )
    process = SimpleNamespace(pid=456, poll=lambda: None)
    popen = MagicMock(return_value=process)
    launcher, registry, store = _launcher(
        _application(has_gio_source=False, actions=(action,)),
        popen=popen,
    )
    handle = MagicMock()
    registry.handles["example.desktop"] = handle

    assert launcher.launch_action("example.desktop", "private") is True

    assert popen.call_args.args[0] == [
        "/opt/example/bin/example",
        "--private",
    ]
    handle.launch_action.assert_not_called()
    store.record_launch.assert_called_once()


def test_new_window_falls_back_to_direct_launch_after_gio_error(monkeypatch):
    action = ApplicationAction(
        action_id="new-window",
        name="New Window",
        sources=frozenset({ActionSource.GIO}),
    )
    popen = MagicMock(return_value=SimpleNamespace(pid=12, poll=lambda: None))
    launcher, registry, store = _launcher(
        _application(actions=(action,)),
        popen=popen,
    )
    handle = MagicMock()
    handle.launch_action.side_effect = RuntimeError("gio failed")
    registry.handles["example.desktop"] = handle
    monkeypatch.setattr(launcher_mod.GLib, "Error", RuntimeError, raising=False)

    assert launcher.launch_new_window("example.desktop") is True
    popen.assert_called_once()
    store.record_launch.assert_called_once()


def test_uri_gio_error_returns_false_without_provenance(monkeypatch):
    launcher, registry, store = _launcher(_application())
    handle = MagicMock()
    handle.launch_uris.side_effect = RuntimeError("gio failed")
    registry.handles["example.desktop"] = handle
    monkeypatch.setattr(launcher_mod.GLib, "Error", RuntimeError, raising=False)

    assert (
        launcher.launch_app_uris(
            "example.desktop",
            ["file:///tmp/document.txt"],
        )
        is False
    )
    store.record_launch.assert_not_called()


def test_idless_listing_uses_opaque_token_without_exposing_handle():
    launcher, registry, store = _launcher(_application())
    handle = MagicMock()
    registry.listing_handles["gio-idless:0"] = handle

    assert launcher.launch_listing("gio-idless:0") is True
    assert launcher.launch_listing("missing") is False

    handle.launch.assert_called_once_with([], None)
    store.record_launch.assert_not_called()


def test_missing_app_empty_uris_and_spawn_errors_return_false():
    popen = MagicMock(side_effect=OSError("boom"))
    launcher, _registry, store = _launcher(_application(), popen=popen)

    assert launcher.launch("missing.desktop") is False
    assert launcher.launch_app_uris("example.desktop", []) is False
    assert launcher.launch("example.desktop") is False
    store.record_launch.assert_not_called()
