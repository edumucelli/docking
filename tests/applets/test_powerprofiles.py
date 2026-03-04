"""Tests for power profiles applet and backend helpers."""

from __future__ import annotations

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
