"""Read the actual BSD guest's kernel APIs; these are not mocked checks."""

from pathlib import Path

import pytest

from docking.applets.network.implementations.bsd import backend as network_bsd
from docking.applets.systemmonitor.implementations.bsd import backend as system_bsd
from docking.applets.trash.implementations.unix import backend as trash_backend
from docking.platform import environment

pytestmark = pytest.mark.skipif(
    not environment.is_bsd(), reason="requires a native BSD kernel"
)


def test_actual_kernel_cpu_memory_and_mounts():
    sample = system_bsd.cpu_sample()
    assert sample is not None
    total, idle = sample
    assert total > 0 and 0 <= idle <= total
    memory = system_bsd.memory_fraction()
    assert memory is not None and 0 <= memory <= 1
    assert Path("/") in trash_backend._mount_points()
    assert any(path == "/" and 0 <= used <= 1 for path, used in system_bsd.disk_usage())


def test_actual_network_counters_are_available():
    interfaces = network_bsd.network_interfaces()
    assert interfaces, "VM network interface was not readable"
    assert all(i.received >= 0 and i.sent >= 0 for i in interfaces)


def test_actual_x11_idle_time_is_available():
    from docking.platform.backends.x11.impl.idle_time import _get_idle_ms

    idle = _get_idle_ms()
    assert idle is not None and idle >= 0
