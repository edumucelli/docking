"""Tests for X11 idle service."""

from __future__ import annotations

import builtins
import ctypes
import sys
import types

from docking.platform.backends.x11.impl import idle_time
from docking.platform.backends.x11.services import idle as idle_service
from docking.platform.backends.x11.services.idle import X11IdleService


def _reset_idle_module() -> None:
    idle_time._xlib = None
    idle_time._xss = None
    idle_time._loaded = False


class _FakeCFunc:
    def __init__(self, func):
        self._func = func
        self.restype = None
        self.argtypes = None

    def __call__(self, *args):
        return self._func(*args)


class _FakeXlib:
    def __init__(self) -> None:
        self.freed: list[object] = []
        self.XDefaultRootWindow = _FakeCFunc(lambda _display: 99)
        self.XFree = _FakeCFunc(lambda ptr: self.freed.append(ptr))


class _FakeXss:
    def __init__(self, *, idle_ms: int = 1234, query_ok: bool = True) -> None:
        info = idle_time._XScreenSaverInfo()
        info.idle = idle_ms
        self.info_ptr = ctypes.pointer(info)
        self.query_ok = query_ok
        self.XScreenSaverAllocInfo = _FakeCFunc(lambda: self.info_ptr)
        self.XScreenSaverQueryInfo = _FakeCFunc(
            lambda _display, _root, _info: int(self.query_ok)
        )


def test_idle_seconds_converts_xss_milliseconds(monkeypatch):
    monkeypatch.setattr(idle_service, "_get_idle_ms", lambda: 2500)

    assert X11IdleService().idle_seconds() == 2.5


def test_idle_seconds_returns_none_when_probe_unavailable(monkeypatch):
    monkeypatch.setattr(idle_service, "_get_idle_ms", lambda: None)

    assert X11IdleService().idle_seconds() is None


class TestIdleProbe:
    def setup_method(self):
        _reset_idle_module()

    def test_load_libraries_failure_is_cached(self, monkeypatch):
        calls: list[str] = []

        def fail(name: str):
            calls.append(name)
            raise OSError("missing")

        monkeypatch.setattr(idle_time.ctypes.cdll, "LoadLibrary", fail)

        assert idle_time._load_libraries() is False
        assert idle_time._load_libraries() is False
        assert calls == ["libX11.so.6"]

    def test_load_libraries_success_configures_functions(self, monkeypatch):
        xlib = _FakeXlib()
        xss = _FakeXss()
        monkeypatch.setattr(
            idle_time.ctypes.cdll,
            "LoadLibrary",
            lambda name: xlib if "X11" in name else xss,
        )

        assert idle_time._load_libraries() is True
        assert idle_time._load_libraries() is True
        assert xlib.XDefaultRootWindow.argtypes == [ctypes.c_void_p]
        assert xss.XScreenSaverQueryInfo.restype is ctypes.c_int

    def test_xdisplay_handle_import_failure(self, monkeypatch):
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "gi":
                raise ImportError("missing gi")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        assert idle_time._xdisplay_handle() is None

    def test_xdisplay_handle_none_and_success(self, monkeypatch):
        gi = types.ModuleType("gi")
        gi.require_version = lambda *_args: None
        repository = types.ModuleType("gi.repository")

        class _Display:
            def get_xdisplay(self):
                return 123

        gdk_x11 = types.SimpleNamespace(
            X11Display=types.SimpleNamespace(get_default=lambda: None)
        )
        repository.GdkX11 = gdk_x11
        monkeypatch.setitem(sys.modules, "gi", gi)
        monkeypatch.setitem(sys.modules, "gi.repository", repository)

        assert idle_time._xdisplay_handle() is None

        gdk_x11.X11Display.get_default = lambda: _Display()
        handle = idle_time._xdisplay_handle()
        assert isinstance(handle, ctypes.c_void_p)
        assert handle.value == 123

    def test_get_idle_ms_success_and_failures(self, monkeypatch):
        xlib = _FakeXlib()
        xss = _FakeXss(idle_ms=4242)
        monkeypatch.setattr(
            idle_time.ctypes.cdll,
            "LoadLibrary",
            lambda name: xlib if "X11" in name else xss,
        )
        monkeypatch.setattr(idle_time, "_xdisplay_handle", lambda: ctypes.c_void_p(1))

        assert idle_time._get_idle_ms() == 4242
        assert xlib.freed == [xss.info_ptr]

        _reset_idle_module()
        monkeypatch.setattr(idle_time, "_load_libraries", lambda: False)
        assert idle_time._get_idle_ms() is None

        monkeypatch.setattr(idle_time, "_load_libraries", lambda: True)
        idle_time._xlib = xlib
        idle_time._xss = xss
        monkeypatch.setattr(idle_time, "_xdisplay_handle", lambda: None)
        assert idle_time._get_idle_ms() is None

        monkeypatch.setattr(idle_time, "_xdisplay_handle", lambda: ctypes.c_void_p(1))
        null_ptr = ctypes.POINTER(idle_time._XScreenSaverInfo)()
        xss.XScreenSaverAllocInfo = _FakeCFunc(lambda: null_ptr)
        assert idle_time._get_idle_ms() is None

        xss = _FakeXss(query_ok=False)
        idle_time._xss = xss
        assert idle_time._get_idle_ms() is None
        assert xlib.freed[-1] == xss.info_ptr
