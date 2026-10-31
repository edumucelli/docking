"""Safe adaptation of external application handles and path identities."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any


def safe_call(
    target: object | None,
    method_name: str,
    *args: object,
) -> Any:
    if target is None:
        return None
    method = getattr(target, method_name, None)
    if not callable(method):
        return None
    try:
        return method(*args)
    except Exception:
        return None


def source_text(value: object) -> str:
    return "" if value is None else str(value)


def desktop_id(app_info: object) -> str:
    return _plain_text(
        safe_call(app_info, "get_id") or getattr(app_info, "desktop_id", "")
    )


def desktop_filename(app_info: object) -> str:
    return source_text(safe_call(app_info, "get_filename"))


def desktop_file_keys(path: Path) -> tuple[Path, ...]:
    """Return exact-expanded and canonical keys without changing source rank."""
    expanded = path.expanduser()
    keys = [expanded]
    try:
        canonical = expanded.resolve(strict=False)
    except (OSError, RuntimeError):
        canonical = None
    if canonical is not None and canonical != expanded:
        keys.append(canonical)
    return tuple(keys)


def unique_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[Path] = set()
    for value in paths:
        path = Path(value).expanduser()
        if path not in seen:
            seen.add(path)
            result.append(path)
    return tuple(result)


def _plain_text(value: object) -> str:
    return "" if value is None else str(value).strip()
