"""BSD integration regression tests at their owning boundaries."""

from docking.applets.thermals.implementations.bsd import backend as bsd


def test_openbsd_sensors_ignore_voltages_and_fans(monkeypatch):
    monkeypatch.setattr(bsd.sys, "platform", "openbsd7")
    monkeypatch.setattr(
        bsd,
        "command",
        lambda argv: (
            "hw.sensors.cpu0.temp0=47.00 degC\nhw.sensors.acpitz0.temp0=45.00 degC\nhw.sensors.it0.fan0=800 RPM"
        ),
    )
    assert bsd.temperatures() == [
        ("hw.sensors.cpu0.temp0", 47.0),
        ("hw.sensors.acpitz0.temp0", 45.0),
    ]
