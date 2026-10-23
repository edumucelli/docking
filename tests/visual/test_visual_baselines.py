"""Screenshot-based visual regression tests for stable dock states."""

from __future__ import annotations

import pytest

from tests.visual.render_cases import VISUAL_CASES, render_case
from tests.visual.support import assert_surface_matches_baseline


@pytest.mark.visual
@pytest.mark.parametrize("case_name", VISUAL_CASES)
def test_visual_baseline(case_name: str, request) -> None:
    surface = render_case(case_name=case_name)
    assert_surface_matches_baseline(
        request=request,
        case_name=case_name,
        surface=surface,
    )
