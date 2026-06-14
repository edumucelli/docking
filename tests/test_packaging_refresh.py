from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_debian_package_installs_refresh_helper():
    install = (ROOT / "packaging/deb/debian/install").read_text(encoding="utf-8")
    assert (
        "packaging/shared/refresh-desktop-caches.sh usr/lib/docking/refresh-desktop-caches"
        in install
    )
    helper = ROOT / "packaging/shared/refresh-desktop-caches.sh"
    assert helper.exists()
    assert helper.stat().st_mode & 0o111


def test_debian_maintainer_scripts_call_refresh_helper():
    postinst = (ROOT / "packaging/deb/debian/postinst").read_text(encoding="utf-8")
    postrm = (ROOT / "packaging/deb/debian/postrm").read_text(encoding="utf-8")
    assert "sh /usr/lib/docking/refresh-desktop-caches" in postinst
    assert "sh /usr/lib/docking/refresh-desktop-caches" in postrm


def test_rpm_package_installs_and_invokes_refresh_helper():
    spec = (ROOT / "packaging/rpm/docking.spec").read_text(encoding="utf-8")
    assert "packaging/shared/refresh-desktop-caches.sh" in spec
    assert "%post" in spec
    assert "%postun" in spec
    assert "/usr/lib/docking/refresh-desktop-caches" in spec


def test_packaging_uses_shared_canonical_desktop_entry():
    canonical = ROOT / "packaging/shared/org.docking.Docking.desktop"
    assert canonical.exists()

    install = (ROOT / "packaging/deb/debian/install").read_text(encoding="utf-8")
    flatpak = (ROOT / "packaging/flatpak/cc.docking.Docking.json").read_text(
        encoding="utf-8"
    )
    snap = (ROOT / "packaging/snap/snapcraft.yaml").read_text(encoding="utf-8")
    rpm = (ROOT / "packaging/rpm/docking.spec").read_text(encoding="utf-8")
    arch = (ROOT / "packaging/arch/PKGBUILD").read_text(encoding="utf-8")
    nix = (ROOT / "packaging/nix/default.nix").read_text(encoding="utf-8")
    appimage = (ROOT / "packaging/appimage/AppImageBuilder.yml").read_text(
        encoding="utf-8"
    )

    expected = "packaging/shared/org.docking.Docking.desktop"
    assert expected in install
    assert "packaging/flatpak/cc.docking.Docking.desktop" in flatpak
    assert "$CRAFT_PROJECT_DIR/../shared/org.docking.Docking.desktop" in snap
    assert expected in rpm
    assert expected in arch
    assert expected in appimage
    assert "../shared/org.docking.Docking.desktop" in nix
    assert "printf '%s\\n'" not in snap


def test_application_desktop_entries_do_not_disable_autostart():
    desktop_entries = (
        ROOT / "packaging/shared/org.docking.Docking.desktop",
        ROOT / "packaging/deb/org.docking.Docking.desktop",
        ROOT / "packaging/flatpak/cc.docking.Docking.desktop",
    )

    for desktop_entry in desktop_entries:
        content = desktop_entry.read_text(encoding="utf-8")
        assert "X-GNOME-Autostart-enabled" not in content
