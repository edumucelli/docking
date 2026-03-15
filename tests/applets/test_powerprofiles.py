"""Tests for power profiles applet and backend helpers."""

from __future__ import annotations

import subprocess
from dataclasses import replace

import docking.applets.powerprofiles.applet as powerprofiles_applet_mod
import docking.applets.powerprofiles.state as powerprofiles_state_mod
from docking.applets.powerprofiles import (
    NullPowerProfilesBackend,
    PowerProfilesApplet,
    PowerProfilesBackend,
    PowerProfilesState,
    TlpBackend,
    TunedBackend,
    create_power_profiles_icon,
    detect_backend,
    normalize_profile,
    order_profiles,
    profile_label,
    tooltip_text,
    unavailable_state,
)


def _state(**overrides: object) -> PowerProfilesState:
    base = PowerProfilesState(
        available=True,
        active_profile="balanced",
        profiles=("power-saver", "balanced", "performance"),
        degraded_reason="",
        error="",
    )
    values = {
        field: getattr(base, field) for field in PowerProfilesState.__dataclass_fields__
    }
    values.update(overrides)
    return PowerProfilesState(**values)


class TestStateHelpers:
    def test_normalize_profile_aliases(self):
        assert normalize_profile("power saver") == "power-saver"
        assert normalize_profile("Perf") == "performance"

    def test_profile_label(self):
        assert profile_label("power-saver") == "Power Saver"
        assert profile_label("balanced") == "Balanced"
        assert profile_label("performance") == "Performance"
        assert profile_label("") == "Unknown"
        assert profile_label("ultra-power") == "Ultra Power"

    def test_order_profiles(self):
        ordered = order_profiles(("performance", "balanced", "power-saver"))
        assert ordered == ("power-saver", "balanced", "performance")

    def test_tooltip_text(self):
        text = tooltip_text(_state(active_profile="performance"))
        assert "Current: Performance" in text
        assert "Available:" in text

    def test_unavailable_tooltip(self):
        text = tooltip_text(unavailable_state(error="no daemon"))
        assert "unavailable" in text.lower()
        assert "no daemon" in text

    def test_tooltip_text_with_degraded_reason(self):
        text = tooltip_text(_state(degraded_reason="Fallback backend"))
        assert "Limited: Fallback backend" in text


class TestPowerProfilesBackend:
    def test_get_state_parses_properties(self):
        backend = object.__new__(PowerProfilesBackend)
        backend._has_service_owner = lambda: True  # type: ignore[attr-defined]
        backend._get_all_properties = lambda: {  # type: ignore[attr-defined]
            "ActiveProfile": "balanced",
            "Profiles": (
                {"Profile": "performance"},
                {"Profile": "balanced"},
                {"Profile": "power-saver"},
            ),
        }
        state = backend.get_state()
        assert state.available is True
        assert state.active_profile == "balanced"
        assert state.profiles == ("power-saver", "balanced", "performance")

    def test_get_state_unavailable_when_no_owner(self):
        backend = object.__new__(PowerProfilesBackend)
        backend._has_service_owner = lambda: False  # type: ignore[attr-defined]
        state = backend.get_state()
        assert state.available is False

    def test_set_active_profile(self):
        backend = object.__new__(PowerProfilesBackend)
        calls: list[tuple[str, str]] = []

        def fake_set_property(**kwargs):
            calls.append((kwargs["property_name"], kwargs["value"]))
            return True

        backend._set_property = fake_set_property  # type: ignore[attr-defined]
        assert backend.set_active_profile("performance") is True
        assert calls == [("ActiveProfile", "performance")]

    def test_set_active_profile_rejects_empty_profile(self):
        backend = object.__new__(PowerProfilesBackend)
        backend._set_property = lambda **kwargs: True  # type: ignore[attr-defined]
        assert backend.set_active_profile("   ") is False

    def test_has_service_owner_handles_none_proxy(self):
        backend = object.__new__(PowerProfilesBackend)
        backend._dbus_proxy = None
        assert backend._has_service_owner() is False

    def test_has_service_owner_handles_proxy_error(self, monkeypatch):
        backend = object.__new__(PowerProfilesBackend)

        class _Proxy:
            def call_sync(self, *_args, **_kwargs):
                raise Exception("dbus fail")

        backend._dbus_proxy = _Proxy()
        monkeypatch.setattr(powerprofiles_state_mod.GLib, "Error", Exception)
        assert backend._has_service_owner() is False

    def test_get_all_properties_returns_none_without_bus(self):
        backend = object.__new__(PowerProfilesBackend)
        backend._bus = None
        assert backend._get_all_properties() is None

    def test_get_all_properties_handles_empty_and_non_dict_payload(self):
        backend = object.__new__(PowerProfilesBackend)

        class _Result:
            def __init__(self, payload):
                self._payload = payload

            def unpack(self):
                return self._payload

        class _Bus:
            def __init__(self):
                self.calls = 0

            def call_sync(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    return _Result(())
                return _Result(("not-a-dict",))

        backend._bus = _Bus()
        assert backend._get_all_properties() == {}
        assert backend._get_all_properties() == {}

    def test_set_property_handles_missing_bus_and_errors(self, monkeypatch):
        backend = object.__new__(PowerProfilesBackend)
        backend._bus = None
        assert (
            backend._set_property(
                interface="i",
                property_name="ActiveProfile",
                signature="s",
                value="balanced",
            )
            is False
        )

        class _Bus:
            def call_sync(self, *_args, **_kwargs):
                raise Exception("boom")

        backend._bus = _Bus()
        monkeypatch.setattr(powerprofiles_state_mod.GLib, "Error", Exception)
        assert (
            backend._set_property(
                interface="i",
                property_name="ActiveProfile",
                signature="s",
                value="balanced",
            )
            is False
        )

    def test_extract_profiles_accepts_alt_keys_and_active_fallback(self):
        profiles = PowerProfilesBackend._extract_profiles(
            props={
                "Profiles": (
                    {"profile": "performance"},
                    {"Name": "powersave"},
                    {"name": "balanced"},
                    "bad",
                )
            },
            active_profile="performance",
        )
        assert profiles == ("power-saver", "balanced", "performance")

        only_active = PowerProfilesBackend._extract_profiles(
            props={"Profiles": "invalid"},
            active_profile="balanced",
        )
        assert only_active == ("balanced",)


class TestFallbackBackends:
    def test_detect_backend_prefers_ppd(self, monkeypatch):
        monkeypatch.setattr(
            powerprofiles_state_mod.PowerProfilesBackend,
            "get_state",
            lambda self: _state(),
        )
        backend = detect_backend()
        assert isinstance(backend, PowerProfilesBackend)

    def test_detect_backend_falls_back_to_tuned(self, monkeypatch):
        monkeypatch.setattr(
            powerprofiles_state_mod.PowerProfilesBackend,
            "get_state",
            lambda self: unavailable_state(),
        )
        monkeypatch.setattr(
            powerprofiles_state_mod, "_has_command", lambda cmd: cmd == "tuned-adm"
        )
        monkeypatch.setattr(
            powerprofiles_state_mod.TunedBackend,
            "get_state",
            lambda self: _state(
                active_profile="balanced",
                degraded_reason="Fallback backend: tuned-adm",
            ),
        )
        backend = detect_backend()
        assert isinstance(backend, TunedBackend)

    def test_detect_backend_falls_back_to_tlp(self, monkeypatch):
        monkeypatch.setattr(
            powerprofiles_state_mod.PowerProfilesBackend,
            "get_state",
            lambda self: unavailable_state(),
        )
        monkeypatch.setattr(
            powerprofiles_state_mod,
            "_has_command",
            lambda cmd: cmd in {"tlp", "tlp-stat"},
        )
        monkeypatch.setattr(
            powerprofiles_state_mod.TlpBackend,
            "get_state",
            lambda self: _state(
                active_profile="power-saver",
                degraded_reason="Fallback backend: tlp mode mapping",
            ),
        )
        backend = detect_backend()
        assert isinstance(backend, TlpBackend)

    def test_detect_backend_returns_null_when_none_available(self, monkeypatch):
        monkeypatch.setattr(
            powerprofiles_state_mod.PowerProfilesBackend,
            "get_state",
            lambda self: unavailable_state(),
        )
        monkeypatch.setattr(powerprofiles_state_mod, "_has_command", lambda cmd: False)
        backend = detect_backend()
        assert isinstance(backend, NullPowerProfilesBackend)

    def test_null_backend_state_and_set(self):
        backend = NullPowerProfilesBackend()
        state = backend.get_state()
        assert state.available is False
        assert "supported backend" in state.error.lower()
        assert backend.set_active_profile("balanced") is False

    def test_tuned_backend_parses_list_and_active(self, monkeypatch):
        backend = TunedBackend()

        def fake_run(cmd: list[str], timeout_s: float = 2.5) -> str | None:
            _ = timeout_s
            if cmd == ["tuned-adm", "active"]:
                return "Current active profile: throughput-performance\n"
            if cmd == ["tuned-adm", "list"]:
                return (
                    "Available profiles:\n"
                    "- balanced\n"
                    "- powersave\n"
                    "- throughput-performance\n"
                )
            return None

        monkeypatch.setattr(powerprofiles_state_mod, "_run", fake_run)
        monkeypatch.setattr(
            powerprofiles_state_mod, "_has_command", lambda cmd: cmd == "tuned-adm"
        )
        state = backend.get_state()
        assert state.available is True
        assert state.active_profile == "performance"
        assert state.profiles == ("power-saver", "balanced", "performance")

    def test_tlp_backend_maps_mode(self, monkeypatch):
        backend = TlpBackend()
        monkeypatch.setattr(
            powerprofiles_state_mod,
            "_has_command",
            lambda cmd: cmd in {"tlp", "tlp-stat"},
        )
        monkeypatch.setattr(
            powerprofiles_state_mod,
            "_run",
            lambda cmd, timeout_s=2.5: (
                "Mode = battery\n" if cmd == ["tlp-stat", "-s"] else ""
            ),
        )
        state = backend.get_state()
        assert state.available is True
        assert state.active_profile == "power-saver"

    def test_tuned_backend_unavailable_without_binary(self, monkeypatch):
        backend = TunedBackend()
        monkeypatch.setattr(powerprofiles_state_mod, "_has_command", lambda _cmd: False)
        state = backend.get_state()
        assert state.available is False
        assert "tuned-adm not available" in state.error

    def test_tuned_backend_unavailable_when_no_supported_profiles(self, monkeypatch):
        backend = TunedBackend()
        monkeypatch.setattr(powerprofiles_state_mod, "_has_command", lambda _cmd: True)
        monkeypatch.setattr(backend, "_active_profile_name", lambda: "")
        monkeypatch.setattr(backend, "_available_profile_names", lambda: ("custom",))
        state = backend.get_state()
        assert state.available is False
        assert "profile data unavailable" in state.error

    def test_tuned_backend_defaults_active_to_first_profile(self, monkeypatch):
        backend = TunedBackend()
        monkeypatch.setattr(powerprofiles_state_mod, "_has_command", lambda _cmd: True)
        monkeypatch.setattr(backend, "_active_profile_name", lambda: "")
        monkeypatch.setattr(
            backend, "_available_profile_names", lambda: ("balanced", "powersave")
        )
        state = backend.get_state()
        assert state.available is True
        assert state.active_profile == "power-saver"

    def test_tuned_set_active_profile_rejects_invalid_and_missing_target(
        self, monkeypatch
    ):
        backend = TunedBackend()
        monkeypatch.setattr(backend, "_available_profile_names", lambda: ("balanced",))
        monkeypatch.setattr(
            backend,
            "_select_tuned_profile_name",
            lambda canonical, available_profiles: "",
        )
        assert backend.set_active_profile("unknown") is False
        assert backend.set_active_profile("performance") is False

    def test_tuned_set_active_profile_runs_command(self, monkeypatch):
        backend = TunedBackend()
        monkeypatch.setattr(backend, "_available_profile_names", lambda: ("balanced",))
        monkeypatch.setattr(
            backend,
            "_select_tuned_profile_name",
            lambda canonical, available_profiles: "balanced",
        )
        monkeypatch.setattr(
            powerprofiles_state_mod,
            "_run",
            lambda cmd, timeout_s=4.0: "",
        )
        assert backend.set_active_profile("balanced") is True

    def test_tuned_active_profile_name_and_available_names_parsers(self, monkeypatch):
        backend = TunedBackend()
        monkeypatch.setattr(
            powerprofiles_state_mod,
            "_run",
            lambda cmd, timeout_s=3.0: (
                "noise\n"
                if cmd == ["tuned-adm", "active"]
                else "- balanced\n- balanced\n- powersave extra\nfoo\n"
            ),
        )
        assert backend._active_profile_name() == ""
        assert backend._available_profile_names() == ("balanced", "powersave")

    def test_tuned_select_profile_prefers_explicit_then_token_fallback(self):
        backend = TunedBackend()
        assert (
            backend._select_tuned_profile_name(
                canonical="performance",
                available_profiles=("balanced", "throughput-performance"),
            )
            == "throughput-performance"
        )
        assert (
            backend._select_tuned_profile_name(
                canonical="power-saver",
                available_profiles=("balanced", "my-power-save-mode"),
            )
            == "my-power-save-mode"
        )
        assert (
            backend._select_tuned_profile_name(
                canonical="balanced",
                available_profiles=("eco",),
            )
            == ""
        )

    def test_tuned_canonical_profile_mapping(self):
        assert TunedBackend._canonical_profile(name="") == ""
        assert TunedBackend._canonical_profile(name="balanced-laptop") == "balanced"
        assert TunedBackend._canonical_profile(name="power save mode") == "power-saver"
        assert (
            TunedBackend._canonical_profile(name="latency-performance") == "performance"
        )
        assert TunedBackend._canonical_profile(name="custom") == "custom"

    def test_tlp_backend_unavailable_when_commands_absent(self, monkeypatch):
        backend = TlpBackend()
        monkeypatch.setattr(powerprofiles_state_mod, "_has_command", lambda _cmd: False)
        state = backend.get_state()
        assert state.available is False
        assert "tlp not available" in state.error

    def test_tlp_backend_detects_other_status_variants(self, monkeypatch):
        backend = TlpBackend()
        monkeypatch.setattr(powerprofiles_state_mod, "_has_command", lambda _cmd: True)
        monkeypatch.setattr(
            powerprofiles_state_mod,
            "_run",
            lambda cmd, timeout_s=3.0: "Power source = AC\n",
        )
        assert backend.get_state().active_profile == "performance"

        monkeypatch.setattr(
            powerprofiles_state_mod,
            "_run",
            lambda cmd, timeout_s=3.0: "unknown status\n",
        )
        assert backend.get_state().active_profile == "balanced"

    def test_tlp_set_active_profile_branches(self, monkeypatch):
        backend = TlpBackend()
        calls: list[list[str]] = []

        def fake_run(cmd, timeout_s=4.0):
            calls.append(cmd)
            if cmd[-1] == "ac":
                return None
            return ""

        monkeypatch.setattr(powerprofiles_state_mod, "_run", fake_run)
        assert backend.set_active_profile("power-saver") is True
        assert backend.set_active_profile("balanced") is True
        assert backend.set_active_profile("performance") is False
        assert backend.set_active_profile("invalid") is False
        assert calls == [["tlp", "bat"], ["tlp", "start"], ["tlp", "ac"]]


class _StubBackend:
    def __init__(self, state: PowerProfilesState) -> None:
        self.state = state
        self.set_calls: list[str] = []

    def get_state(self) -> PowerProfilesState:
        return self.state

    def set_active_profile(self, profile: str) -> bool:
        self.set_calls.append(profile)
        self.state = replace(self.state, active_profile=profile)
        return True


def _make_applet(
    monkeypatch,
    state: PowerProfilesState,
) -> tuple[PowerProfilesApplet, _StubBackend]:
    backend = _StubBackend(state=state)
    monkeypatch.setattr(powerprofiles_applet_mod, "detect_backend", lambda: backend)
    applet = PowerProfilesApplet(48)
    return applet, backend


class TestPowerProfilesApplet:
    def test_creates_with_icon(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        assert applet.item.icon is not None

    def test_click_cycles_profile(self, monkeypatch):
        applet, backend = _make_applet(monkeypatch, _state(active_profile="balanced"))
        applet._set_profile_async = lambda *, profile: backend.set_active_profile(  # type: ignore[assignment]
            profile
        )
        applet.on_clicked()
        assert backend.set_calls == ["performance"]

    def test_click_noop_when_unavailable_or_single_profile(self, monkeypatch):
        applet, backend = _make_applet(monkeypatch, unavailable_state())
        applet._set_profile_async = lambda *, profile: backend.set_calls.append(profile)  # type: ignore[assignment]
        applet.on_clicked()

        applet2, backend2 = _make_applet(
            monkeypatch,
            _state(active_profile="balanced", profiles=("balanced",)),
        )
        applet2._set_profile_async = lambda *, profile: backend2.set_calls.append(  # type: ignore[assignment]
            profile
        )
        applet2.on_clicked()
        assert backend.set_calls == []
        assert backend2.set_calls == []

    def test_unavailable_menu(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, unavailable_state())
        items = applet.get_menu_items()
        assert len(items) == 1
        assert "unavailable" in (items[0].get_label() or "").lower()
        assert items[0].get_sensitive() is False

    def test_menu_has_profile_entries(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        labels = [
            item.get_label() for item in applet.get_menu_items() if item.get_label()
        ]
        assert "Select Profile" in labels
        assert "Power Saver" in labels
        assert "Balanced" in labels
        assert "Performance" in labels

    def test_menu_shows_degraded_reason(self, monkeypatch):
        applet, _backend = _make_applet(
            monkeypatch,
            _state(degraded_reason="Fallback backend: tlp mode mapping"),
        )
        labels = [item.get_label() for item in applet.get_menu_items()]
        assert any(
            label == "Limited: Fallback backend: tlp mode mapping" for label in labels
        )

    def test_ordered_profiles_fallbacks(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state(profiles=()))
        applet._state = _state(active_profile="balanced", profiles=())
        assert applet._ordered_profiles() == ("balanced",)
        applet._state = _state(active_profile="", profiles=())
        assert applet._ordered_profiles() == ("balanced", "performance", "power-saver")

    def test_profile_toggle_dispatch_rules(self, monkeypatch):
        applet, backend = _make_applet(monkeypatch, _state(active_profile="balanced"))
        calls: list[str] = []
        applet._set_profile_async = lambda *, profile: calls.append(profile)  # type: ignore[assignment]

        class _Widget:
            def __init__(self, active: bool):
                self._active = active

            def get_active(self):
                return self._active

        applet._on_profile_toggled(_Widget(active=False), "performance")
        applet._on_profile_toggled(_Widget(active=True), "balanced")
        applet._on_profile_toggled(_Widget(active=True), "performance")
        assert calls == ["performance"]
        assert backend.set_calls == []

    def test_poll_result_refreshes_only_on_change(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        refresh_calls: list[str] = []
        applet.present = lambda: refresh_calls.append("refresh")  # type: ignore[assignment]
        state = _state()
        assert applet._on_poll_result(state) is False
        assert refresh_calls == []
        changed = _state(active_profile="performance")
        assert applet._on_poll_result(changed) is False
        assert refresh_calls == ["refresh"]

    def test_set_result_sets_error_on_failure(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        refresh_calls: list[str] = []
        applet.present = lambda: refresh_calls.append("refresh")  # type: ignore[assignment]
        applet._set_in_progress = True
        assert applet._on_set_result("performance", False, _state()) is False
        assert applet._set_in_progress is False
        assert "Failed to set Performance" in applet._action_error
        assert refresh_calls == ["refresh"]

    def test_set_profile_async_guards_in_progress(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        applet._set_in_progress = True
        applet._set_profile_async(profile="performance")
        assert applet._set_in_progress is True

    def test_refresh_tooltip_includes_action_error(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        applet._action_error = "Failed"
        applet.refresh_tooltip()
        assert "Failed" in applet.item.name

    def test_start_stop_tick_and_poll_worker(self, monkeypatch):
        applet, backend = _make_applet(monkeypatch, _state())
        removed: list[int] = []
        monkeypatch.setattr(
            powerprofiles_applet_mod.GLib,
            "timeout_add_seconds",
            lambda sec, cb: 77,
        )
        monkeypatch.setattr(
            powerprofiles_applet_mod.GLib,
            "source_remove",
            lambda source_id: removed.append(source_id),
        )
        applet.start(notify=lambda: None)
        assert applet._poll_id == 77
        applet.stop()
        assert applet._poll_id == 0
        assert removed == [77]

        calls: list[str] = []

        def fake_run_guarded(*, key, name, fn, on_result=None, on_error=None):
            _ = name, on_error
            calls.append(key)
            result = fn()
            if on_result is not None:
                on_result(result)
            return True

        applet._worker.run_guarded = fake_run_guarded  # type: ignore[method-assign]
        assert applet._tick() is True
        assert calls == ["poll"]
        assert applet._poll_worker() == backend.get_state()

    def test_on_clicked_handles_unknown_active_profile(self, monkeypatch):
        applet, backend = _make_applet(
            monkeypatch,
            _state(
                active_profile="unknown-profile",
                profiles=("power-saver", "balanced", "performance"),
            ),
        )
        applet._set_profile_async = lambda *, profile: backend.set_calls.append(profile)  # type: ignore[assignment]
        applet.on_clicked()
        assert backend.set_calls == ["power-saver"]

    def test_set_profile_async_worker_and_success_result(self, monkeypatch):
        applet, backend = _make_applet(monkeypatch, _state())
        applet._set_in_progress = False
        run_calls: list[dict[str, object]] = []

        def fake_run(**kwargs):
            run_calls.append(kwargs)

        applet._worker.run = fake_run  # type: ignore[method-assign]
        applet._set_profile_async(profile="performance")
        assert applet._set_in_progress is True
        assert run_calls
        assert run_calls[0]["name"] == "powerprofiles-set"
        success, state = run_calls[0]["fn"]()
        assert success is True
        assert state.active_profile == "performance"

        applet.present = lambda: None  # type: ignore[assignment]
        applet._action_error = "old"
        assert applet._on_set_result("performance", True, _state()) is False
        assert applet._action_error == ""

    def test_set_profile_async_error_clears_in_progress(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        applet.present = lambda: None  # type: ignore[assignment]

        def fake_run(**kwargs):
            kwargs["on_error"](RuntimeError("boom"))

        applet._worker.run = fake_run  # type: ignore[method-assign]
        applet._set_profile_async(profile="performance")

        assert applet._set_in_progress is False
        assert "Failed to set Performance" in applet._action_error


class TestPowerProfilesStateHelpers:
    def test_run_helper_branches(self, monkeypatch):
        class _Proc:
            def __init__(self, code: int, out: str):
                self.returncode = code
                self.stdout = out

        monkeypatch.setattr(
            powerprofiles_state_mod.subprocess,
            "run",
            lambda *args, **kwargs: _Proc(0, "ok"),
        )
        assert powerprofiles_state_mod._run(["echo"]) == "ok"

        monkeypatch.setattr(
            powerprofiles_state_mod.subprocess,
            "run",
            lambda *args, **kwargs: _Proc(1, "nope"),
        )
        assert powerprofiles_state_mod._run(["echo"]) is None

        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="x", timeout=1)

        monkeypatch.setattr(powerprofiles_state_mod.subprocess, "run", raise_timeout)
        assert powerprofiles_state_mod._run(["echo"]) is None

    def test_has_command_and_supported_profiles(self, monkeypatch):
        monkeypatch.setattr(powerprofiles_state_mod.shutil, "which", lambda cmd: None)
        assert powerprofiles_state_mod._has_command("x") is False
        monkeypatch.setattr(
            powerprofiles_state_mod.shutil, "which", lambda cmd: "/bin/x"
        )
        assert powerprofiles_state_mod._has_command("x") is True
        assert powerprofiles_state_mod._supported_profiles() == (
            "power-saver",
            "balanced",
            "performance",
        )

    def test_unpack_and_as_str_helpers(self):
        class _Variant:
            def __init__(self, value):
                self._value = value

            def unpack(self):
                return self._value

        class _BrokenVariant:
            def unpack(self):
                raise RuntimeError("bad")

        unpacked = powerprofiles_state_mod._unpack(
            {"k": _Variant([_Variant("v"), 2]), "t": (_Variant("x"),)}
        )
        assert unpacked == {"k": ["v", 2], "t": ("x",)}
        broken = _BrokenVariant()
        assert powerprofiles_state_mod._unpack(broken) is broken
        assert powerprofiles_state_mod._as_str(None) == ""
        assert powerprofiles_state_mod._as_str(_Variant("hello")) == "hello"


class TestPowerProfilesRender:
    def test_icon_renders(self):
        for profile in ("power-saver", "balanced", "performance"):
            pixbuf = create_power_profiles_icon(
                size=48,
                profile=profile,
                available=True,
            )
            assert pixbuf is not None
            assert pixbuf.get_width() == 48
            assert pixbuf.get_height() == 48
