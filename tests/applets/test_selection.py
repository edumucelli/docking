"""Selection remains stable across feed reorderings and missing records."""

import pytest

from docking.applets.selection import retained_index


@pytest.mark.parametrize(
    ("ids", "current_id", "previous_index", "expected"),
    (
        ((3, 1, 2), 2, 0, 2),
        ((1, 2), 3, 99, 1),
        (("a", "a", "b"), "a", 1, 0),
        ((), "gone", 99, 0),
        ((0, 1), 0, 1, 0),
        (("", "two"), None, 1, 1),
        (("one", "two"), None, -5, 0),
    ),
)
def test_retained_index(ids, current_id, previous_index, expected):
    assert (
        retained_index(ids, current_id=current_id, previous_index=previous_index)
        == expected
    )
