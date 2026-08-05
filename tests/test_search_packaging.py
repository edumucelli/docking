"""Packaging invariants for global search assets and bus ownership."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_catalog_icon_and_documentation_are_packaged() -> None:
    project = (ROOT / "pyproject.toml").read_text()

    assert "docking-search" not in project
    assert (ROOT / "docking/assets/icons/applets/search.png").is_file()
    assert (ROOT / "docs/SEARCH.md").is_file()


def test_flatpak_owns_package_bus_name_without_external_search_action() -> None:
    manifest = json.loads(
        (ROOT / "packaging/flatpak/cc.docking.Docking.json").read_text()
    )
    desktop = (ROOT / "packaging/flatpak/cc.docking.Docking.desktop").read_text()

    assert "--own-name=cc.docking.Docking" in manifest["finish-args"]
    assert "docking-search" not in desktop


def test_host_and_snap_packages_do_not_reference_removed_search_command() -> None:
    shared_desktop = (ROOT / "packaging/shared/org.docking.Docking.desktop").read_text()
    snapcraft = (ROOT / "packaging/snap/snapcraft.yaml").read_text()

    assert "docking-search" not in shared_desktop
    assert "docking-search" not in snapcraft
    assert "interface: dbus" in snapcraft
    assert "name: org.docking.Docking" in snapcraft
