"""Checklist items and their local preference representation."""

from dataclasses import dataclass


@dataclass
class ChecklistItem:
    text: str
    checked: bool = False


def items_from_prefs(value: object) -> list[ChecklistItem]:
    if not isinstance(value, list):
        return []

    items = []
    for row in value:
        if not isinstance(row, dict):
            continue
        text = row.get("text")
        if isinstance(text, str) and text.strip():
            items.append(
                ChecklistItem(text.strip(), checked=row.get("checked") is True)
            )
    items.sort(key=lambda item: item.checked)
    return items


def items_to_prefs(items: list[ChecklistItem]) -> list[dict[str, str | bool]]:
    return [{"text": item.text, "checked": item.checked} for item in items]
