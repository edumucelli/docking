from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    script = (
        Path(__file__).resolve().parents[1] / "tools" / "check_translation_packaging.py"
    )
    spec = importlib.util.spec_from_file_location("check_translation_packaging", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_check_translation_packaging_passes_on_expected_layout(tmp_path):
    mod = _load_module()

    _write(
        tmp_path / "pyproject.toml",
        """
[tool.setuptools.package-data]
docking = ["locale/*/LC_MESSAGES/*.mo"]
""".strip()
        + "\n",
    )
    _write(
        tmp_path / "setup.cfg",
        """
[options.package_data]
docking =
    locale/*/LC_MESSAGES/*.mo
""".strip()
        + "\n",
    )
    _write(tmp_path / "packaging/deb/debian/rules", "bash tools/i18n.sh --compile\n")
    _write(tmp_path / "packaging/rpm/docking.spec", "bash tools/i18n.sh --compile\n")
    _write(
        tmp_path / "packaging/flatpak/org.docking.Docking.json",
        "bash tools/i18n.sh --compile\n",
    )
    _write(
        tmp_path / "packaging/snap/snapcraft.yaml",
        'bash "$CRAFT_PART_SRC/tools/i18n.sh" --compile\n',
    )
    _write(
        tmp_path / "packaging/appimage/build.sh",
        'bash "${ROOT_DIR}/tools/i18n.sh" --compile\n',
    )
    _write(
        tmp_path / "packaging/arch/build.sh",
        'bash "${ROOT_DIR}/tools/i18n.sh" --compile\n',
    )
    _write(
        tmp_path / "packaging/nix/build.sh",
        'bash "${ROOT_DIR}/tools/i18n.sh" --compile\n',
    )
    _write(tmp_path / ".github/workflows/ci.yml", "bash tools/i18n.sh --compile\n")

    assert mod.check_translation_packaging(tmp_path) == []


def test_check_translation_packaging_reports_missing_steps(tmp_path):
    mod = _load_module()

    _write(
        tmp_path / "pyproject.toml",
        """
[tool.setuptools.package-data]
docking = []
""".strip()
        + "\n",
    )
    _write(tmp_path / "setup.cfg", "[options.package_data]\ndocking =\n")
    _write(tmp_path / "packaging/deb/debian/rules", "")
    _write(tmp_path / "packaging/rpm/docking.spec", "")
    _write(tmp_path / "packaging/flatpak/org.docking.Docking.json", "")
    _write(tmp_path / "packaging/snap/snapcraft.yaml", "")
    _write(tmp_path / "packaging/appimage/build.sh", "")
    _write(tmp_path / "packaging/arch/build.sh", "")
    _write(tmp_path / "packaging/nix/build.sh", "")
    _write(tmp_path / ".github/workflows/ci.yml", "")

    errors = mod.check_translation_packaging(tmp_path)
    assert any("Compiled .mo catalogs" in error for error in errors)
    assert any("packaging/deb/debian/rules" in error for error in errors)
    assert any(".github/workflows/ci.yml" in error for error in errors)
