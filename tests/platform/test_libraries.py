"""Native library discovery must respect BSD ABI names and Linux SONAMEs."""

import pytest

from docking.platform import libraries


@pytest.mark.parametrize("system", ["freebsd15", "openbsd7"])
def test_bsd_uses_native_library_name(monkeypatch, system):
    libraries._library_path.cache_clear()
    monkeypatch.setattr(libraries.sys, "platform", system)
    monkeypatch.setattr(
        libraries.ctypes.util,
        "find_library",
        lambda name: f"/usr/local/lib/lib{name}.so.18.2",
    )
    monkeypatch.setattr(libraries.ctypes.cdll, "LoadLibrary", lambda path: path)
    assert (
        libraries.load_library("X11", "libX11.so.6") == "/usr/local/lib/libX11.so.18.2"
    )
    libraries._library_path.cache_clear()


def test_linux_keeps_packaged_soname(monkeypatch):
    libraries._library_path.cache_clear()
    monkeypatch.setattr(libraries.sys, "platform", "linux")
    monkeypatch.setattr(
        libraries.ctypes.util,
        "find_library",
        lambda _: pytest.fail("unexpected discovery"),
    )
    monkeypatch.setattr(libraries.ctypes.cdll, "LoadLibrary", lambda path: path)
    assert libraries.load_library("X11", "libX11.so.6") == "libX11.so.6"
    libraries._library_path.cache_clear()
