"""Application backend selection keeps native implementations isolated."""

import subprocess
import sys
from pathlib import Path

import pytest

from docking.platform.applications import catalog_backend, launch_backend
from docking.platform.applications.unix_catalog import UnixApplicationCatalogBackend
from docking.platform.applications.unix_launch import UnixApplicationLaunchBackend
from docking.platform.applications.windows.catalog import (
    WindowsApplicationCatalogBackend,
)
from docking.platform.applications.windows.launch import WindowsApplicationLaunchBackend


@pytest.mark.parametrize("windows", [False, True])
def test_catalog_and_launch_factories_select_matching_platform(monkeypatch, windows):
    monkeypatch.setattr(catalog_backend.environment, "is_windows", lambda: windows)
    assert isinstance(
        catalog_backend.create_catalog_backend(),
        WindowsApplicationCatalogBackend if windows else UnixApplicationCatalogBackend,
    )
    assert isinstance(
        launch_backend.create_launch_backend(),
        WindowsApplicationLaunchBackend if windows else UnixApplicationLaunchBackend,
    )


@pytest.mark.parametrize("windows", [False, True])
def test_factory_selection_never_imports_the_other_os_implementation(windows):
    root = Path(__file__).resolve().parents[3]
    blocked = (
        (
            "docking.platform.applications.unix_catalog",
            "docking.platform.applications.unix_launch",
        )
        if windows
        else (
            "docking.platform.applications.windows",
            "docking.platform.backends.windows",
        )
    )
    code = f"""
import importlib.abc
import sys
sys.path.insert(0, {str(root)!r})
class RejectOtherPlatform(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in {blocked!r}):
            raise AssertionError("Unselected platform imported: " + fullname)
sys.meta_path.insert(0, RejectOtherPlatform())
from docking.platform.applications import catalog_backend, launch_backend
catalog_backend.environment.is_windows = lambda: {windows!r}
catalog = catalog_backend.create_catalog_backend()
launch = launch_backend.create_launch_backend()
print(type(catalog).__name__, type(launch).__name__)
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
