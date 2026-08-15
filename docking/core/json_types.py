"""JSON-compatible type aliases shared by persistence and IPC boundaries."""

from __future__ import annotations

from typing import TypeAlias, cast

JsonScalar: TypeAlias = bool | int | float | str | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


def as_json_object(value: JsonValue) -> JsonObject | None:
    """Return *value* as a JSON object when it has string keys."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return None
    return cast(JsonObject, value)
