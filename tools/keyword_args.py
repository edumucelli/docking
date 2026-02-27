"""Codemod: convert positional arguments to keyword arguments at call sites.

Finds all function/method definitions in the project, builds a param-name
map keyed by (simple_name, param_count), then rewrites call sites to use
keyword=value style. The param_count key prevents mismatches when multiple
functions share the same name (e.g. different __init__ methods).

Skips:
- Calls where positional arg count doesn't exactly match a known signature
- Arguments that are already keyword-style
- *args / **kwargs splat arguments
- super().__init__() calls (parent class may have different param names)

Usage:
    python tools/keyword_args.py [--dry-run] [--min N] [path ...]

If no paths given, processes docking/ and tests/.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

import libcst as cst


def _collect_signatures(root: Path, min_params: int) -> dict[tuple[str, int], list[str]]:
    """Scan all .py files and collect function signatures.

    Returns {(func_name, param_count): [param_names]}.
    Using (name, count) as key prevents collisions between different
    functions with the same name but different signatures.
    """
    sigs: dict[tuple[str, int], list[str]] = {}

    for py_file in sorted(root.rglob("*.py")):
        try:
            source = py_file.read_text()
            tree = cst.parse_module(source)
        except Exception:
            continue

        for node in _walk(tree):
            if isinstance(node, cst.FunctionDef):
                params = _extract_param_names(node)
                if len(params) >= min_params:
                    name = node.name.value
                    key = (name, len(params))
                    # First definition wins (avoids overwrite by subclass)
                    if key not in sigs:
                        sigs[key] = params

    return sigs


def _walk(node: cst.CSTNode):
    """Yield all nodes in the CST tree."""
    yield node
    for child in node.children:
        yield from _walk(child)


def _extract_param_names(func: cst.FunctionDef) -> list[str]:
    """Extract regular parameter names (excluding self/cls)."""
    names = []
    for param in func.params.params:
        name = param.name.value
        if name in ("self", "cls"):
            continue
        names.append(name)
    return names


class KeywordArgTransformer(cst.CSTTransformer):
    """Rewrite positional call arguments to keyword style."""

    def __init__(
        self,
        sigs: dict[tuple[str, int], list[str]],
        min_positional: int,
    ) -> None:
        self._sigs = sigs
        self._min = min_positional
        self._changes = 0

    @property
    def changes(self) -> int:
        return self._changes

    def leave_Call(
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.Call:
        func_name = self._get_func_name(updated_node.func)
        if not func_name:
            return updated_node

        # Skip super().__init__() — parent may have different param names
        if func_name == "__init__" and self._is_super_call(updated_node.func):
            return updated_node

        args = list(updated_node.args)

        # Count positional-only args (no keyword, no star)
        positional_count = sum(
            1 for a in args if a.keyword is None and a.star == ""
        )
        if positional_count < self._min:
            return updated_node

        # Look up by (name, exact_positional_count) to avoid mismatches
        params = self._sigs.get((func_name, positional_count))
        if not params:
            return updated_node

        new_args = []
        param_idx = 0
        for arg in args:
            if arg.keyword is not None or arg.star != "":
                new_args.append(arg)
                continue

            if param_idx < len(params):
                new_arg = arg.with_changes(
                    keyword=cst.Name(params[param_idx]),
                    equal=cst.AssignEqual(
                        whitespace_before=cst.SimpleWhitespace(""),
                        whitespace_after=cst.SimpleWhitespace(""),
                    ),
                )
                new_args.append(new_arg)
                param_idx += 1
                self._changes += 1
            else:
                new_args.append(arg)
                param_idx += 1

        return updated_node.with_changes(args=new_args)

    # stdlib/third-party module names whose methods we must not rewrite
    _STDLIB_OBJECTS = frozenset({
        "json", "os", "sys", "math", "time", "re", "ast", "io",
        "Path", "datetime", "GLib", "Gtk", "Gdk", "Gio", "GdkPixbuf",
        "Wnck", "Pango", "PangoCairo", "cairo", "GdkX11",
    })

    # Only convert method calls on these receivers (known to be project code)
    _SAFE_RECEIVERS = frozenset({
        "self", "cls",
    })

    def _get_func_name(self, func: cst.BaseExpression) -> str | None:
        if isinstance(func, cst.Name):
            # Bare function call: compute_layout(), launch(), etc.
            return func.value
        if isinstance(func, cst.Attribute):
            # Method call: only convert self.method() calls.
            # Skip obj.method() for arbitrary objects since we can't
            # verify the receiver's type (may be GTK/stdlib/etc.)
            if isinstance(func.value, cst.Name):
                if func.value.value in self._SAFE_RECEIVERS:
                    return func.attr.value
            return None
        return None

    def _is_super_call(self, func: cst.BaseExpression) -> bool:
        """Check if call is super().something()."""
        if not isinstance(func, cst.Attribute):
            return False
        obj = func.value
        if isinstance(obj, cst.Call) and isinstance(obj.func, cst.Name):
            return obj.func.value == "super"
        return False


def process_file(
    path: Path,
    sigs: dict[tuple[str, int], list[str]],
    min_positional: int,
    dry_run: bool = False,
) -> int:
    source = path.read_text()
    try:
        tree = cst.parse_module(source)
    except Exception as e:
        print(f"  skip {path}: {e}", file=sys.stderr)
        return 0

    transformer = KeywordArgTransformer(sigs, min_positional)
    new_tree = tree.visit(transformer)

    if transformer.changes > 0 and not dry_run:
        path.write_text(new_tree.code)

    return transformer.changes


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Convert positional to keyword args")
    parser.add_argument("paths", nargs="*", default=["docking", "tests"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min", type=int, default=2, help="Min positional args to convert")
    args = parser.parse_args(argv)

    print("Scanning signatures...")
    sigs = _collect_signatures(Path("docking"), args.min)
    print(f"  Found {len(sigs)} function signatures with {args.min}+ params")

    total = 0
    for path_str in args.paths:
        root = Path(path_str)
        py_files = sorted(root.rglob("*.py")) if root.is_dir() else [root]
        for py_file in py_files:
            n = process_file(py_file, sigs, args.min, dry_run=args.dry_run)
            if n > 0:
                label = "(dry run)" if args.dry_run else ""
                print(f"  {py_file}: {n} args converted {label}")
                total += n

    print(f"\nTotal: {total} args converted")


if __name__ == "__main__":
    main()
