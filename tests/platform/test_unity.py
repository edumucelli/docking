"""Tests for Unity LauncherEntry integration."""

from __future__ import annotations

from unittest.mock import MagicMock

from gi.repository import GLib

import docking.platform.unity as unity_mod
from docking.platform.applications.identity import (
    parse_application_uri as parse_application_identity_uri,
)


class _FakeConnection:
    def __init__(self) -> None:
        self.subscriptions: list[tuple[object, ...]] = []
        self.unsubscribed: list[int] = []

    def signal_subscribe(
        self,
        sender,
        interface_name,
        member,
        object_path,
        arg0,
        flags,
        callback,
    ) -> int:
        self.subscriptions.append(
            (sender, interface_name, member, object_path, arg0, flags, callback)
        )
        return len(self.subscriptions)

    def signal_unsubscribe(self, subscription_id: int) -> None:
        self.unsubscribed.append(subscription_id)


def _variant_payload(
    *,
    app_uri: str = "application://firefox.desktop",
    count: int = 4,
    count_visible: bool = True,
    progress: float = 0.25,
    progress_visible: bool = True,
    urgent: bool = True,
) -> GLib.Variant:
    return GLib.Variant(
        "(sa{sv})",
        (
            app_uri,
            {
                "count": GLib.Variant("x", count),
                "count-visible": GLib.Variant("b", count_visible),
                "progress": GLib.Variant("d", progress),
                "progress-visible": GLib.Variant("b", progress_visible),
                "urgent": GLib.Variant("b", urgent),
            },
        ),
    )


def _registry():
    registry = MagicMock()
    registry.resolve.return_value = None
    return registry


class TestParseApplicationUri:
    def test_valid_uri_returns_desktop_id(self):
        assert unity_mod.parse_application_uri is parse_application_identity_uri
        assert unity_mod.parse_application_uri("application://firefox.desktop") == (
            "firefox.desktop"
        )

    def test_invalid_uris_return_none(self):
        assert unity_mod.parse_application_uri("firefox.desktop") is None
        assert unity_mod.parse_application_uri("application://") is None
        assert unity_mod.parse_application_uri("application://nested/path.desktop") is (
            None
        )
        assert unity_mod.parse_application_uri("application://firefox") is None


class TestUnityLauncherListener:
    def test_start_and_stop_manage_bus_subscriptions(self, monkeypatch):
        model = MagicMock()
        connection = _FakeConnection()
        unowned: list[int] = []

        monkeypatch.setattr(unity_mod.Gio, "bus_get_sync", lambda *_args: connection)
        monkeypatch.setattr(unity_mod.Gio, "bus_own_name", lambda *_args: 77)
        monkeypatch.setattr(
            unity_mod.Gio, "bus_unown_name", lambda owner_id: unowned.append(owner_id)
        )

        listener = unity_mod.UnityLauncherListener(
            model=model,
            application_registry=_registry(),
        )
        listener.start()
        listener.stop()

        assert len(connection.subscriptions) == 2
        assert connection.unsubscribed == [1, 2]
        assert unowned == [77]

    def test_parse_payload_extracts_normalized_state(self):
        listener = unity_mod.UnityLauncherListener(
            model=MagicMock(),
            application_registry=_registry(),
        )

        state = listener._parse_payload(
            sender_name=":1.7",
            parameters=_variant_payload(progress=1.4),
        )

        assert state is not None
        assert state.sender_name == ":1.7"
        assert state.desktop_id == "firefox.desktop"
        assert state.badge_count == 4
        assert state.badge_visible is True
        assert state.progress == 1.0
        assert state.progress_visible is True
        assert state.urgent is True

    def test_parse_payload_uses_canonical_id_for_exact_registry_match(self):
        registry = _registry()
        registry.resolve.return_value = MagicMock(
            desktop_id="org.mozilla.firefox.desktop"
        )
        listener = unity_mod.UnityLauncherListener(
            model=MagicMock(),
            application_registry=registry,
        )

        state = listener._parse_payload(
            sender_name=":1.7",
            parameters=_variant_payload(),
        )

        assert state is not None
        assert state.desktop_id == "org.mozilla.firefox.desktop"
        registry.resolve.assert_called_once_with(
            "firefox.desktop",
            log_failures=False,
        )

    def test_parse_payload_keeps_unresolved_id_without_alias_broadening(self):
        registry = _registry()
        listener = unity_mod.UnityLauncherListener(
            model=MagicMock(),
            application_registry=registry,
        )

        state = listener._parse_payload(
            sender_name=":1.7",
            parameters=_variant_payload(app_uri="application://Vendor-Firefox.desktop"),
        )

        assert state is not None
        assert state.desktop_id == "Vendor-Firefox.desktop"
        registry.resolve.assert_called_once_with(
            "Vendor-Firefox.desktop",
            log_failures=False,
        )
        registry.resolve_by_wm_class.assert_not_called()

    def test_invalid_payload_signature_is_ignored(self):
        listener = unity_mod.UnityLauncherListener(
            model=MagicMock(),
            application_registry=_registry(),
        )

        state = listener._parse_payload(
            sender_name=":1.7",
            parameters=GLib.Variant("(s)", ("firefox.desktop",)),
        )

        assert state is None

    def test_first_miss_schedules_idle_retry_for_transient_creation(self, monkeypatch):
        model = MagicMock()
        model.apply_launcher_entry.side_effect = [False, True]
        idle_calls: list[tuple[object, tuple[object, ...]]] = []

        def _idle_add(callback, *args):
            idle_calls.append((callback, args))
            return 33

        monkeypatch.setattr(unity_mod.GLib, "idle_add", _idle_add)
        monkeypatch.setattr(unity_mod.GLib, "get_monotonic_time", lambda: 100_000)

        listener = unity_mod.UnityLauncherListener(
            model=model,
            application_registry=_registry(),
        )
        listener._handle_update(sender_name=":1.7", parameters=_variant_payload())

        assert (
            model.apply_launcher_entry.call_args_list[0].kwargs["create_transient"]
            is False
        )
        assert idle_calls

        callback, args = idle_calls[0]
        callback(*args)

        assert (
            model.apply_launcher_entry.call_args_list[1].kwargs["create_transient"]
            is True
        )

    def test_name_owner_changed_removes_sender_state(self):
        model = MagicMock()
        listener = unity_mod.UnityLauncherListener(
            model=model,
            application_registry=_registry(),
        )
        listener._entries[":1.7"] = unity_mod._SenderEntry()

        listener._on_name_owner_changed(
            None,
            "",
            "",
            "",
            "",
            GLib.Variant("(sss)", (":1.7", "before", "")),
        )

        model.remove_launcher_entry.assert_called_once_with(sender_name=":1.7")
        assert ":1.7" not in listener._entries

    def test_bursty_sender_is_throttled(self, monkeypatch):
        model = MagicMock()
        timeout_calls: list[tuple[int, object, tuple[object, ...]]] = []
        times = iter([0, 10_000, 20_000, 25_000, 26_000])

        monkeypatch.setattr(unity_mod.GLib, "get_monotonic_time", lambda: next(times))
        monkeypatch.setattr(
            unity_mod.GLib,
            "timeout_add",
            lambda delay, callback, *args: (
                timeout_calls.append((delay, callback, args)) or 55
            ),
        )

        listener = unity_mod.UnityLauncherListener(
            model=model,
            application_registry=_registry(),
        )
        payload = _variant_payload()
        for _ in range(5):
            listener._handle_update(sender_name=":1.7", parameters=payload)

        assert timeout_calls
        assert timeout_calls[0][0] == unity_mod.THROTTLE_WINDOW_MS
