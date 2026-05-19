#!/usr/bin/env python3
"""Classify Vulture findings by whether names are referenced only in tests."""

from __future__ import annotations

import argparse
import tokenize
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import vulture
from vulture.core import Item


@dataclass(frozen=True)
class NameOccurrence:
    path: Path
    line: int


@dataclass(frozen=True)
class ClassifiedItem:
    item: Item
    category: str
    production_refs: tuple[NameOccurrence, ...]
    test_refs: tuple[NameOccurrence, ...]


def _python_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix == ".py":
            files.append(path)
            continue
        if path.is_dir():
            files.extend(sorted(path.rglob("*.py")))
    return files


def _name_index(paths: list[Path]) -> dict[str, list[NameOccurrence]]:
    index: dict[str, list[NameOccurrence]] = defaultdict(list)
    for path in _python_files(paths):
        with path.open("rb") as handle:
            try:
                tokens = tokenize.tokenize(handle.readline)
            except tokenize.TokenError:
                continue
            for token in tokens:
                if token.type != tokenize.NAME:
                    continue
                index[token.string].append(
                    NameOccurrence(path=path, line=token.start[0])
                )
    return index


def _classify_item(
    *,
    item: Item,
    prod_index: dict[str, list[NameOccurrence]],
    test_index: dict[str, list[NameOccurrence]],
) -> ClassifiedItem:
    name = str(item.name)
    item_path = Path(item.filename).resolve()
    item_line = int(item.first_lineno)

    def is_other_ref(occurrence: NameOccurrence) -> bool:
        resolved = occurrence.path.resolve()
        return not (resolved == item_path and occurrence.line == item_line)

    production_refs = tuple(
        occurrence
        for occurrence in prod_index.get(name, [])
        if is_other_ref(occurrence)
    )
    test_refs = tuple(
        occurrence
        for occurrence in test_index.get(name, [])
        if is_other_ref(occurrence)
    )
    if production_refs:
        category = "production_refs"
    elif test_refs:
        category = "tests_only_refs"
    else:
        category = "no_other_refs"
    return ClassifiedItem(
        item=item,
        category=category,
        production_refs=production_refs,
        test_refs=test_refs,
    )


def _format_occurrence(root: Path, occurrence: NameOccurrence) -> str:
    try:
        rel = occurrence.path.resolve().relative_to(root.resolve())
    except ValueError:
        rel = occurrence.path
    return f"{rel}:{occurrence.line}"


def _report_line(root: Path, classified: ClassifiedItem) -> str:
    item = classified.item
    item_path = Path(item.filename).resolve()
    try:
        rel = item_path.relative_to(root.resolve())
    except ValueError:
        rel = item_path
    refs = classified.test_refs if classified.category == "tests_only_refs" else ()
    ref_text = ""
    if refs:
        shown = ", ".join(_format_occurrence(root, ref) for ref in refs[:3])
        extra = max(len(refs) - 3, 0)
        if extra:
            shown += f", +{extra} more"
        ref_text = f" | refs in tests: {shown}"
    return (
        f"[{classified.category}] {rel}:{item.first_lineno} "
        f"{item.typ} '{item.name}' ({item.confidence}% confidence){ref_text}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--production-path",
        action="append",
        default=["docking"],
        help="Path to production Python package(s) to scan with Vulture.",
    )
    parser.add_argument(
        "--tests-path",
        action="append",
        default=["tests"],
        help="Path to test Python package(s) used for reference classification.",
    )
    parser.add_argument(
        "--min-confidence",
        type=int,
        default=60,
        help="Minimum Vulture confidence to include in the report.",
    )
    parser.add_argument(
        "--show-production-refs",
        action="store_true",
        help="Also print findings that have production references.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    production_paths = [root / path for path in args.production_path]
    test_paths = [root / path for path in args.tests_path]

    scanner = vulture.Vulture()
    scanner.scavenge([str(path) for path in production_paths])
    items = [
        item
        for item in scanner.get_unused_code()
        if int(item.confidence) >= args.min_confidence
    ]

    prod_index = _name_index(production_paths)
    test_index = _name_index(test_paths)
    classified = [
        _classify_item(
            item=item,
            prod_index=prod_index,
            test_index=test_index,
        )
        for item in items
    ]

    test_only = [entry for entry in classified if entry.category == "tests_only_refs"]
    no_other = [entry for entry in classified if entry.category == "no_other_refs"]
    prod_refs = [entry for entry in classified if entry.category == "production_refs"]

    print(
        "[test-only-code] "
        f"scanned={len(items)} tests_only={len(test_only)} "
        f"no_other_refs={len(no_other)} production_refs={len(prod_refs)}"
    )
    for entry in test_only:
        print(_report_line(root, entry))
    for entry in no_other:
        print(_report_line(root, entry))
    if args.show_production_refs:
        for entry in prod_refs:
            print(_report_line(root, entry))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
