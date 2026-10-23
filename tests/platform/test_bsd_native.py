"""Read the actual BSD guest's kernel APIs; these are not mocked checks."""

from pathlib import Path

import pytest

from docking.platform import bsd, environment

pytestmark = pytest.mark.skipif(
    not environment.is_bsd(), reason="requires a native BSD kernel"
)


def test_actual_kernel_cpu_memory_and_mounts():
    sample = bsd.cpu_sample()
    assert sample is not None
    total, idle = sample
    assert total > 0 and 0 <= idle <= total
    memory = bsd.memory_fraction()
    assert memory is not None and 0 <= memory <= 1
    assert Path("/") in bsd.mount_points()
    assert any(path == "/" and 0 <= used <= 1 for path, used in bsd.disk_usage())


def test_actual_network_counters_are_available():
    interfaces = bsd.network_interfaces()
    assert interfaces, "VM network interface was not readable"
    assert all(i.received >= 0 and i.sent >= 0 for i in interfaces)
