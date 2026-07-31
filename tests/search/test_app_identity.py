"""Tests for the package identity used by the Search shortcut portal."""

from docking.search.app_identity import application_id


def test_application_id_uses_host_identity_outside_flatpak() -> None:
    assert application_id(env={}) == "org.docking.Docking"


def test_application_id_uses_flatpak_identity_inside_sandbox() -> None:
    assert application_id(env={"FLATPAK_ID": "cc.docking.Docking"}) == (
        "cc.docking.Docking"
    )
