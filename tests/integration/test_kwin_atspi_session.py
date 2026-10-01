"""Real message-bus isolation, restart and late-service regressions for F12."""

import shutil

import pytest

from tests.bdd_support.atspi_session import AccessibilitySession

pytestmark = pytest.mark.skipif(
    shutil.which("dbus-daemon") is None, reason="Needs dbus-daemon"
)


def test_concurrent_sessions_never_mix_accessible_windows():
    first = AccessibilitySession("Alpha")
    try:
        second = AccessibilitySession("Beta")
        try:
            a = first.wait_for_titles(["Alpha"])
            b = second.wait_for_titles(["Beta"])
            assert a["address"] != b["address"]
        finally:
            second.close()
    finally:
        first.close()


def test_missing_bus_recovers_then_reconnects_to_new_address():
    session = AccessibilitySession("Late", available=False)
    try:
        assert session.wait_for_titles([])["running"]
        session.request("available")
        old = session.wait_for_titles(["Late"])
        session.request("missing")
        session.wait_for_titles([])
        session.request("available")
        new = session.wait_for_titles(["Late"])
        assert old["address"] != new["address"]
        session.request("stop")
        assert session.request("snapshot")["titles"] == []
        session.request("restart")
        assert session.request("snapshot")["titles"] == []
        session.request("start")
        session.wait_for_titles(["Late"])
    finally:
        session.close()


def test_explicit_address_works_when_discovery_is_unavailable():
    session = AccessibilitySession("Explicit", available=False, explicit=True)
    try:
        session.wait_for_titles(["Explicit"])
    finally:
        session.close()
