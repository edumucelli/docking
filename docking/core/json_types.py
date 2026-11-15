"""JSON-compatible type aliases shared by persistence and IPC boundaries."""

from __future__ import annotations

from typing import TypeAlias

JsonScalar: TypeAlias = bool | int | float | str | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


def as_json_object(value: JsonValue) -> JsonObject | None:
    """Return *value* as a JSON object when it has string keys."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return None
    return value


def coerce_bool(value: object, *, default: bool) -> bool:
    """Decode a persisted boolean without treating arbitrary values as truthy."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default
