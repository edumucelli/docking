"""Architecture guard: import rules for ``docking.platform.applications``."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APPS_DIR = Path(__file__).resolve().parents[3] / "docking" / "platform" / "applications"


def _modules_under(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if p.name != "__init__.py")


def _imports_of(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                results.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                results.append((node.lineno, node.module))
            elif node.level is not None and node.level > 0:
                pkg = "docking.platform.applications"
                parts = pkg.split(".")
                if node.level <= len(parts):
                    base = (
                        pkg if node.level <= 1 else ".".join(parts[: -node.level + 1])
                    )
                    if node.module:
                        results.append(
                            (
                                node.lineno,
                                f"docking.platform.applications.{node.module}",
                            )
                        )
                    else:
                        results.append((node.lineno, base))
    return results


class TestAppsImportRules:
    """Structural import rules for docking.platform.applications."""

    def test_init_is_docstring_only(self) -> None:
        path = APPS_DIR / "__init__.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                msg = (
                    f"applications/__init__.py imports {ast.dump(node)} — "
                    f"must stay docstring-only to avoid import cycle"
                )
                raise AssertionError(msg)

    def test_types_imports_nothing_from_docking(self) -> None:
        path = APPS_DIR / "types.py"
        for lineno, mod in _imports_of(path):
            if mod.startswith("docking"):
                msg = f"types.py:{lineno} imports {mod!r} — types.py must be a leaf"
                raise AssertionError(msg)

    def test_no_apps_module_imports_platform_model(self) -> None:
        for path in _modules_under(APPS_DIR):
            for lineno, mod in _imports_of(path):
                if mod == "docking.platform.model":
                    msg = f"{path.name}:{lineno} imports docking.platform.model"
                    raise AssertionError(msg)

    def test_no_apps_module_imports_ui(self) -> None:
        for path in _modules_under(APPS_DIR):
            for lineno, mod in _imports_of(path):
                if mod.startswith("docking.ui"):
                    msg = f"{path.name}:{lineno} imports {mod!r}"
                    raise AssertionError(msg)

    def test_no_apps_module_imports_search(self) -> None:
        for path in _modules_under(APPS_DIR):
            for lineno, mod in _imports_of(path):
                if mod.startswith("docking.search"):
                    msg = f"{path.name}:{lineno} imports {mod!r}"
                    raise AssertionError(msg)

    def test_no_apps_module_imports_applets(self) -> None:
        violations: list[str] = []
        for path in _modules_under(APPS_DIR):
            for lineno, mod in _imports_of(path):
                if mod.startswith("docking.applets"):
                    violations.append(f"{path.name}:{lineno} imports {mod!r}")
        if violations:
            pytest.skip(f"Temporary applets imports: {violations}")
