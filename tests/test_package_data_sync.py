from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    script = (
        Path(__file__).resolve().parents[1] / "tools" / "check_package_data_sync.py"
    )
    spec = importlib.util.spec_from_file_location("check_package_data_sync", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_check_package_data_sync_passes_on_matching_inputs(tmp_path):
    mod = _load_module()
    _write(
        tmp_path / "pyproject.toml",
        """
[tool.setuptools.package-data]
docking = ["assets/*.png", "locale/*.pot"]
""".strip()
        + "\n",
    )
    _write(
        tmp_path / "setup.cfg",
        """
[options.package_data]
docking =
    assets/*.png
    locale/*.pot
""".strip()
        + "\n",
    )
    _write(
        tmp_path / "MANIFEST.in",
        """
recursive-include docking/assets *.svg *.png *.json *.csv.gz *.ogg *.md
recursive-include docking/locale *.pot *.po *.mo
""".strip()
        + "\n",
    )

    assert mod.check_package_data_sync(tmp_path) == []


def test_check_package_data_sync_reports_drift(tmp_path):
    mod = _load_module()
    _write(
        tmp_path / "pyproject.toml",
        """
[tool.setuptools.package-data]
docking = ["assets/*.png", "locale/*.pot"]
""".strip()
        + "\n",
    )
    _write(
        tmp_path / "setup.cfg",
        """
[options.package_data]
docking =
    assets/*.png
""".strip()
        + "\n",
    )
    _write(tmp_path / "MANIFEST.in", "recursive-include docking/assets *.png\n")

    errors = mod.check_package_data_sync(tmp_path)

    assert any("pyproject.toml and setup.cfg" in error for error in errors)
    assert any(
        "MANIFEST.in is missing expected recursive includes" in error
        for error in errors
    )
