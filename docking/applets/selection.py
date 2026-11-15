"""Keep a selected feed entry stable when refreshed records are reordered."""

from collections.abc import Iterable

from docking.core.math import clamp_index


def retained_index(
    ids: Iterable[object], *, current_id: object, previous_index: int
) -> int:
    values = tuple(ids)
    if current_id is not None:
        for index, value in enumerate(values):
            if value == current_id:
                return index
    return clamp_index(previous_index, len(values))
