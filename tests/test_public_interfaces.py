"""Shared application imports must use public modules and names."""

import ast
from pathlib import Path


def test_application_imports_use_public_interfaces():
    root = Path(__file__).resolve().parents[1]
    violations = []
    for path in sorted((root / "docking").rglob("*.py")):
        if "protocols" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = (node.module or "").split(".") + [
                    alias.name for alias in node.names
                ]
            elif isinstance(node, ast.Import):
                names = [part for alias in node.names for part in alias.name.split(".")]
            else:
                continue
            # Gettext's conventional `_` and Python's dunder APIs are public.
            if any(
                name.startswith("_") and name != "_" and not name.startswith("__")
                for name in names
            ):
                violations.append(
                    f"{path.relative_to(root)}:{node.lineno}: {ast.unparse(node)}"
                )
    assert not violations, "Private cross-module imports:\n" + "\n".join(violations)
