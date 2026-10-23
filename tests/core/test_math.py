"""Tests for core math helpers."""

from __future__ import annotations

from docking.core.math import clamp, clamp_index, clamp_int


class TestClamp:
    def test_clamp_within_range(self):
        assert clamp(5.0, 0.0, 10.0) == 5.0

    def test_clamp_below_minimum(self):
        assert clamp(-1.0, 0.0, 10.0) == 0.0

    def test_clamp_above_maximum(self):
        assert clamp(15.0, 0.0, 10.0) == 10.0


class TestClampInt:
    def test_clamp_int_within_range(self):
        assert clamp_int(5, 0, 10) == 5

    def test_clamp_int_below_minimum(self):
        assert clamp_int(-1, 0, 10) == 0

    def test_clamp_int_above_maximum(self):
        assert clamp_int(15, 0, 10) == 10


class TestClampIndex:
    def test_clamp_index_within_range(self):
        assert clamp_index(5, 10) == 5

    def test_clamp_index_below_zero(self):
        assert clamp_index(-5, 10) == 0

    def test_clamp_index_above_length(self):
        assert clamp_index(15, 10) == 9

    def test_clamp_index_zero_length(self):
        assert clamp_index(5, 0) == 0

    def test_clamp_index_negative_length(self):
        assert clamp_index(5, -1) == 0

    def test_clamp_index_at_boundary(self):
        assert clamp_index(0, 10) == 0
        assert clamp_index(9, 10) == 9
