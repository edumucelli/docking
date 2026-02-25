"""Tests for the docklet registry."""

from docking.docklets import get_registry
from docking.docklets.base import Docklet


class TestRegistry:
    def test_returns_dict(self):
        registry = get_registry()
        assert isinstance(registry, dict)

    def test_all_values_are_docklet_subclasses(self):
        for docklet_id, cls in get_registry().items():
            assert issubclass(cls, Docklet), f"{docklet_id}: {cls} not a Docklet"

    def test_contains_clock(self):
        assert "clock" in get_registry()

    def test_contains_trash(self):
        assert "trash" in get_registry()

    def test_contains_desktop(self):
        assert "desktop" in get_registry()

    def test_contains_cpumonitor(self):
        assert "cpumonitor" in get_registry()

    def test_contains_battery(self):
        assert "battery" in get_registry()

    def test_cached_returns_same_object(self):
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2
