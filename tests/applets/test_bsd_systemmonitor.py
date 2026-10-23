"""BSD integration regression tests at their owning boundaries."""

from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

from docking.applets.systemmonitor import bsd
from docking.applets.trash import backend as trash_backend


def test_cpu_uses_cumulative_native_times_without_sleeping(monkeypatch):
    times = namedtuple("Times", "user system idle nice irq")(2, 3, 10, 1, 1)
    monkeypatch.setattr(
        bsd, "_psutil", lambda: SimpleNamespace(cpu_times=lambda: times)
    )
    assert bsd.cpu_sample() == (17000, 10000)


def test_memory_percentage_is_normalized(monkeypatch):
    monkeypatch.setattr(
        bsd,
        "_psutil",
        lambda: SimpleNamespace(virtual_memory=lambda: SimpleNamespace(percent=42.5)),
    )
    assert bsd.memory_fraction() == 0.425


def test_mount_table_keeps_zfs_and_deduplicates_mount_points(monkeypatch):
    partitions = [
        SimpleNamespace(mountpoint=p, fstype=fs)
        for p, fs in (
            ("/", "ufs"),
            ("/home", "zfs"),
            ("/home", "zfs"),
            ("/dev", "devfs"),
        )
    ]
    monkeypatch.setattr(
        bsd,
        "_psutil",
        lambda: SimpleNamespace(
            disk_partitions=lambda **kwargs: partitions,
            disk_usage=lambda path: SimpleNamespace(percent=25),
        ),
    )
    monkeypatch.setattr(trash_backend, "_psutil", bsd._psutil)
    assert trash_backend._bsd_mount_points() == (Path("/"), Path("/home"), Path("/dev"))
    assert bsd.disk_usage() == [("/", 0.25), ("/home", 0.25)]
