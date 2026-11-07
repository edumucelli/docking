"""Read the actual BSD guest's kernel APIs; these are not mocked checks."""

import os
import sys
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


def test_actual_host_selection_and_process_executable():
    from docking.platform.applications.implementations.bsd.process import (
        process_executable,
    )
    from docking.platform.composition.bsd import BsdPlatformProvider
    from docking.platform.composition.selection import platform_provider
    from docking.platform.host.base import HostFamily
    from docking.platform.host.selection import host_services

    assert host_services().family is HostFamily.BSD
    assert isinstance(platform_provider(), BsdPlatformProvider)
    assert process_executable(os.getpid()) == Path(sys.executable).resolve()


@pytest.mark.skipif(
    not sys.platform.startswith("netbsd"),
    reason="requires NetBSD pkgsrc desktop dependencies",
)
def test_actual_netbsd_icons_and_application_discovery():
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    gi.require_version("Gtk", "3.0")
    from gi.repository import GdkPixbuf, Gtk

    from docking.platform.applications.implementations.unix.entries import desktop_dirs

    asset = Path(__file__).resolve().parents[2] / "docking/assets/poof.svg"
    assert GdkPixbuf.Pixbuf.new_from_file(str(asset)).get_width() > 0
    assert Gtk.IconTheme.get_default().has_icon("folder")
    assert Path("/usr/pkg/share/applications") in desktop_dirs()
