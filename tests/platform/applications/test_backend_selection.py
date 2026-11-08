"""Application composition keeps native implementations and worker graphs isolated."""

import subprocess
import sys
from pathlib import Path


def test_factory_selection_never_imports_the_other_os_implementation():
    root = Path(__file__).resolve().parents[3]
    blocked = (
        "docking.platform.applications.implementations.windows",
        "docking.platform.native.windows",
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
from docking.platform.composition import selection
from docking.platform.host.implementations.linux.backend import LinuxHostServices
selection.host_services = LinuxHostServices
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
    expected = "Unix"
    assert completed.stdout.split() == [
        expected + "ApplicationCatalogBackend",
        expected + "ApplicationLaunchBackend",
    ]
