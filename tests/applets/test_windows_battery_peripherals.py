"""Windows device-container batteries, unknown values and identity filtering."""

import base64
import json
import sys
from types import SimpleNamespace

import pytest

from docking.applets.battery.implementations.windows import peripherals as mod
from docking.applets.battery.peripherals import PeripheralBattery


def entry(**changes):
    return {
        "id": "{00000000-0000-0000-0000-000000000001}",
        "name": "Fixture keyboard",
        "connected": True,
        "local_machine": False,
        "percent": 75,
        "charging": 0,
        "categories": ["Keyboard"],
        **changes,
    }


def test_known_empty_and_charging_readings_keep_the_reported_values_and_order():
    rows = [
        entry(),
        entry(
            id="00000000-0000-0000-0000-000000000002",
            name="Fixture mouse",
            percent=0,
            categories=["Mouse"],
        ),
        entry(
            id="00000000-0000-0000-0000-000000000003",
            name="Fixture headset",
            percent=25,
            charging=1,
            categories=["Headset"],
        ),
    ]
    assert mod._parse_response({"devices": rows}) == [
        PeripheralBattery("Fixture mouse", "Mouse", 0.0, False, ""),
        PeripheralBattery("Fixture headset", "Headset", 25.0, True, ""),
        PeripheralBattery("Fixture keyboard", "Keyboard", 75.0, False, ""),
    ]


@pytest.mark.parametrize("percent", [None, -1, 101, 255, True, "75", 75.0])
def test_unknown_or_malformed_percentages_are_not_coerced_to_readings(percent):
    assert mod._parse_response({"devices": [entry(percent=percent)]}) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"connected": False},
        {"connected": 1},
        {"local_machine": True},
        {"local_machine": None},
        {"name": ""},
        {"name": "bad\0name"},
        {"id": "not-a-container-id"},
        {"id": "00000000-0000-0000-0000-000000000000"},
    ],
)
def test_disconnected_internal_or_unidentifiable_devices_are_omitted(changes):
    assert mod._parse_response({"devices": [entry(**changes)]}) == []


def test_container_identity_deduplicates_interfaces_without_merging_similar_names():
    rows = [
        entry(),
        entry(id="00000000-0000-0000-0000-000000000001"),
        entry(id="00000000-0000-0000-0000-000000000002"),
    ]
    assert len(mod._parse_response({"devices": rows})) == 2


@pytest.mark.parametrize("charging", [None, 0, 2, 255, True, "1"])
def test_only_the_reported_charging_state_adds_a_charging_indication(charging):
    result = mod._parse_response({"devices": [entry(charging=charging)]})
    assert len(result) == 1 and not result[0].charging


@pytest.mark.parametrize("payload", [None, [], {}, {"devices": None}, {"devices": [1]}])
def test_invalid_provider_responses_are_rejected(payload):
    with pytest.raises(ValueError):
        mod._parse_response(payload)


def test_device_provider_is_bounded_and_never_starts_a_shell(monkeypatch):
    def run(argv, **kwargs):
        assert argv[1:4] == ["-NoProfile", "-NonInteractive", "-EncodedCommand"]
        script = base64.b64decode(argv[4]).decode("utf-16-le")
        assert script == mod._SCRIPT
        assert kwargs["timeout"] == 8 and kwargs["creationflags"] == 0x08000000
        assert "shell" not in kwargs
        return SimpleNamespace(returncode=0, stdout=json.dumps({"devices": [entry()]}))

    monkeypatch.setattr(mod.subprocess, "run", run)
    assert mod.read_peripheral_batteries()[0].percent == 75


def test_failed_provider_does_not_expose_device_output_in_the_error(monkeypatch):
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="unused"),
    )
    with pytest.raises(OSError, match="Windows device battery query failed"):
        mod.read_peripheral_batteries()


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows device containers")
def test_native_device_container_query_returns_only_known_external_battery_values():
    if sys.getwindowsversion().product_type != 1:
        pytest.skip("Device battery acceptance targets Windows clients")
    readings = mod.read_peripheral_batteries()
    assert all(
        item.model and item.percent is not None and 0 <= item.percent <= 100
        for item in readings
    )
