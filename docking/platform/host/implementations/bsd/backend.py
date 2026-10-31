"""Bsd host composition of shared POSIX and kernel-specific filesystem facts."""

from ...base import HostFamily
from ..unix.backend import UnixHostServices
from .mounts import mount_points


class BsdHostServices(UnixHostServices):
    family = HostFamily.BSD
    mount_points = staticmethod(mount_points)
