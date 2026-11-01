"""Safe adaptation of external application handles and path identities."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .types import TransientApplicationInfo


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


def transient_from_gio(
    *,
    app_info: object,
    listing_key: str,
    require_visible: bool,
) -> TransientApplicationInfo | None:
    if require_visible and (
        safe_bool_call(app_info, "get_is_hidden")
        or safe_bool_call(app_info, "get_nodisplay")
    ):
        return None
    icon = safe_call(app_info, "get_icon")
    filename = source_text(safe_call(app_info, "get_filename"))
    application_id = desktop_id(app_info)
    categories_raw = source_text(safe_call(app_info, "get_categories"))
    return TransientApplicationInfo(
        listing_key=listing_key,
        name=(
            source_text(safe_call(app_info, "get_display_name"))
            or application_id
            or "Unknown"
        ),
        categories=normalise_values(categories_raw),
        categories_raw=categories_raw,
        declared_icon=source_text(safe_call(icon, "to_string")),
        desktop_file=Path(filename).expanduser() if filename else None,
        exec_line=source_text(safe_call(app_info, "get_commandline")),
        description=source_text(safe_call(app_info, "get_description")),
        generic_name=source_text(safe_call(app_info, "get_generic_name")),
    )


def normalise_values(value: object | None) -> tuple[str, ...]:
    if value is None:
        return ()
    raw_values: Iterable[object]
    if isinstance(value, str):
        raw_values = value.split(";")
    elif isinstance(value, Iterable):
        raw_values = list(value)
    else:
        raw_values = (value,)
    result: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        clean = clean_text(raw_value)
        key = " ".join(unicodedata.normalize("NFKC", clean).casefold().split())
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
    return tuple(result)


def clean_text(value: object) -> str:
    return "" if value is None else " ".join(str(value).split())


def safe_bool_call(target: object, method_name: str) -> bool:
    return bool(safe_call(target, method_name))
