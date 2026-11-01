"""Application composition keeps native implementations and worker graphs isolated."""

import subprocess
import sys
from pathlib import Path

import pytest

from docking.platform.applications.implementations.unix.catalog import (
    UnixApplicationCatalogBackend,
)
from docking.platform.applications.implementations.unix.launch import (
    UnixApplicationLaunchBackend,
)
from docking.platform.applications.implementations.windows.catalog import (
    WindowsApplicationCatalogBackend,
)
from docking.platform.applications.implementations.windows.launch import (
    WindowsApplicationLaunchBackend,
)
from docking.platform.composition import selection
from docking.platform.composition.windows import WindowsPlatformProvider
from docking.platform.host.implementations.linux.backend import LinuxHostServices
from docking.platform.host.implementations.windows.backend import WindowsHostServices


@pytest.mark.parametrize("windows", [False, True])
def test_catalog_and_launch_factories_select_matching_platform(monkeypatch, windows):
    monkeypatch.setattr(
        selection,
        "host_services",
        WindowsHostServices if windows else LinuxHostServices,
    )
    selection.platform_provider.cache_clear()
    try:
        provider = selection.platform_provider()
        assert isinstance(
            provider.application_sources().catalog,
            WindowsApplicationCatalogBackend
            if windows
            else UnixApplicationCatalogBackend,
        )
        assert isinstance(
            provider.launches(),
            WindowsApplicationLaunchBackend
            if windows
            else UnixApplicationLaunchBackend,
        )
    finally:
        selection.platform_provider.cache_clear()


@pytest.mark.parametrize("windows", [False, True])
def test_factory_selection_never_imports_the_other_os_implementation(windows):
    root = Path(__file__).resolve().parents[3]
    blocked = (
        ("docking.platform.applications.implementations.unix",)
        if windows
        else (
            "docking.platform.applications.implementations.windows",
            "docking.platform.native.windows",
        )
    )
    host = "windows" if windows else "linux"
    class_name = "Windows" if windows else "Linux"
    code = f"""
import importlib.abc
import sys
sys.path.insert(0, {str(root)!r})
class RejectOtherPlatform(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in {blocked!r}):
            raise AssertionError("Unselected platform imported: " + fullname)
sys.meta_path.insert(0, RejectOtherPlatform())
from docking.platform.composition import selection
from docking.platform.host.implementations.{host}.backend import {class_name}HostServices
selection.host_services = {class_name}HostServices
provider = selection.platform_provider()
print(type(provider.application_sources().catalog).__name__, type(provider.launches()).__name__)
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    expected = "Windows" if windows else "Unix"
    assert completed.stdout.split() == [
        expected + "ApplicationCatalogBackend",
        expected + "ApplicationLaunchBackend",
    ]


def test_windows_sources_share_one_worker_only_within_their_own_registry_graph():
    provider = WindowsPlatformProvider()
    first, second = provider.application_sources(), provider.application_sources()
    assert first.catalog.lookup is first.associations._lookup
    assert second.catalog.lookup is second.associations._lookup
    assert first.catalog.lookup is not second.catalog.lookup
    assert first.registrations is not second.registrations
    assert first.catalog.initial_discovery_pending
    assert second.catalog.initial_discovery_pending
