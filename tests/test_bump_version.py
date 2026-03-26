from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_bump_version_module():
    script = Path(__file__).resolve().parents[1] / "tools" / "bump_version.py"
    spec = importlib.util.spec_from_file_location("bump_version", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_bump_version_updates_all_known_surfaces(tmp_path):
    mod = _load_bump_version_module()
    mod.ROOT = tmp_path

    _write(tmp_path / "pyproject.toml", '[project]\nversion = "0.1.1"\n')
    _write(tmp_path / "setup.cfg", "[metadata]\nversion = 0.1.1\n")
    _write(tmp_path / "docking/__init__.py", '__version__ = "0.1.1"\n')
    _write(
        tmp_path / "packaging/rpm/docking.spec",
        "Version:        %{?pkg_version}%{!?pkg_version:0.1.1}\n%changelog\n"
        "* Thu Mar 05 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.1-1\n"
        "- Release 0.1.1.\n",
    )
    _write(tmp_path / "packaging/snap/snapcraft.yaml", 'version: "0.1.1"\n')
    _write(tmp_path / "packaging/arch/PKGBUILD", "pkgver=0.1.1\n")
    _write(tmp_path / "packaging/nix/default.nix", '  version = "0.1.1";\n')
    _write(
        tmp_path / "packaging/deb/debian/changelog",
        "docking (0.1.1-1) stable; urgency=medium\n\n"
        "  * Release 0.1.1.\n\n"
        " -- Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com>  Thu, 05 Mar 2026 20:00:00 +0100\n",
    )
    _write(
        tmp_path / "packaging/flatpak/org.docking.Docking.metainfo.xml",
        '<component>\n  <releases>\n    <release version="0.1.1" date="2026-03-05" />\n  </releases>\n</component>\n',
    )

    mod.bump_version(version="0.2.0")

    assert 'version = "0.2.0"' in (tmp_path / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert "version = 0.2.0" in (tmp_path / "setup.cfg").read_text(encoding="utf-8")
    assert '__version__ = "0.2.0"' in (tmp_path / "docking/__init__.py").read_text(
        encoding="utf-8"
    )
    assert "pkgver=0.2.0" in (tmp_path / "packaging/arch/PKGBUILD").read_text(
        encoding="utf-8"
    )
    assert '  version = "0.2.0";' in (tmp_path / "packaging/nix/default.nix").read_text(
        encoding="utf-8"
    )
    assert 'version: "0.2.0"' in (tmp_path / "packaging/snap/snapcraft.yaml").read_text(
        encoding="utf-8"
    )
    assert "%{!?pkg_version:0.2.0}" in (
        tmp_path / "packaging/rpm/docking.spec"
    ).read_text(encoding="utf-8")
    assert (
        "docking (0.2.0-1) stable; urgency=medium"
        in (tmp_path / "packaging/deb/debian/changelog")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert 'version="0.2.0"' in (
        tmp_path / "packaging/flatpak/org.docking.Docking.metainfo.xml"
    ).read_text(encoding="utf-8")


def test_bump_version_is_idempotent_for_current_version(tmp_path):
    mod = _load_bump_version_module()
    mod.ROOT = tmp_path

    _write(tmp_path / "pyproject.toml", '[project]\nversion = "0.2.0"\n')
    _write(tmp_path / "setup.cfg", "[metadata]\nversion = 0.2.0\n")
    _write(tmp_path / "docking/__init__.py", '__version__ = "0.2.0"\n')
    _write(
        tmp_path / "packaging/rpm/docking.spec",
        "Version:        %{?pkg_version}%{!?pkg_version:0.2.0}\n%changelog\n"
        "* Sun Mar 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.2.0-1\n"
        "- Release 0.2.0.\n",
    )
    _write(tmp_path / "packaging/snap/snapcraft.yaml", 'version: "0.2.0"\n')
    _write(tmp_path / "packaging/arch/PKGBUILD", "pkgver=0.2.0\n")
    _write(tmp_path / "packaging/nix/default.nix", '  version = "0.2.0";\n')
    _write(
        tmp_path / "packaging/deb/debian/changelog",
        "docking (0.2.0-1) stable; urgency=medium\n\n"
        "  * Release 0.2.0.\n\n"
        " -- Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com>  Sun, 08 Mar 2026 11:00:00 +0100\n",
    )
    _write(
        tmp_path / "packaging/flatpak/org.docking.Docking.metainfo.xml",
        '<component>\n  <releases>\n    <release version="0.2.0" date="2026-03-08" />\n  </releases>\n</component>\n',
    )

    before = {
        path: path.read_text(encoding="utf-8")
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    mod.bump_version(version="0.2.0")

    after = {
        path: path.read_text(encoding="utf-8")
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert before == after
