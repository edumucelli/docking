"""Exercise Qt-side message producers and their actual event writer without Qt."""

import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from docking.applets.webapp.implementations.windows.companion import protocol as p
from tests.applets.test_windows_webapp_companion import PROFILE


@pytest.fixture
def runtime_module(monkeypatch):
    # These replace only the optional Qt imports. The Runtime, Page, Output, and
    # codec under test are the production implementations, with real pipe bytes.
    class Widget:
        pass

    class Page(Widget):
        NavigationType = SimpleNamespace(NavigationTypeLinkClicked=1)

    def slot(*_types):
        return lambda callback: callback

    def module(name, **members):
        result = ModuleType(name)
        result.__dict__.update(members)
        monkeypatch.setitem(sys.modules, name, result)

    module("PySide6")
    module(
        "PySide6.QtCore",
        QFile=Widget,
        QIODevice=Widget,
        QLockFile=Widget,
        QObject=Widget,
        Qt=SimpleNamespace(),
        QUrl=Widget,
        Signal=lambda *_args: None,
        Slot=slot,
    )
    module("PySide6.QtGui", QKeySequence=Widget, QShortcut=Widget)
    module("PySide6.QtWebChannel", QWebChannel=Widget)
    module(
        "PySide6.QtWebEngineCore",
        QWebEnginePage=Page,
        QWebEnginePermission=Widget,
        QWebEngineProfile=Widget,
        QWebEngineProfileBuilder=Widget,
        QWebEngineScript=Widget,
    )
    module("PySide6.QtWebEngineWidgets", QWebEngineView=Widget)
    module(
        "PySide6.QtWidgets",
        QApplication=Widget,
        QMainWindow=Widget,
        QMessageBox=Widget,
        QToolBar=Widget,
    )
    companion = "docking.applets.webapp.implementations.windows.companion."
    module(companion + "downloads", Downloads=Widget)
    module(companion + "input", UnicodePackets=Widget)
    module(companion + "notifications", DesktopNotifier=Widget)
    spec = importlib.util.spec_from_file_location(
        "_docking_test_companion_runtime",
        Path(p.__file__).with_name("runtime.py"),
    )
    assert spec is not None and spec.loader is not None
    runtime = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, runtime)
    spec.loader.exec_module(runtime)
    return runtime


@pytest.mark.parametrize("producer", ["link", "new-window"])
@pytest.mark.parametrize("oversized", [False, True])
def test_external_url_producers_leave_event_writer_running(
    runtime_module, monkeypatch, tmp_path, producer, oversized
):
    mod = runtime_module
    stream = io.BytesIO()
    monkeypatch.setattr(
        mod, "sys", SimpleNamespace(stdout=SimpleNamespace(buffer=stream))
    )
    output = mod.Output()
    runtime = mod.Runtime.__new__(mod.Runtime)
    runtime.configuration = p.Configuration(PROFILE, directory=tmp_path)
    runtime._last = {}
    runtime._send = output.send
    uri = "https://example.test/" + ("x" * 32768 if oversized else "valid")
    url = SimpleNamespace(toString=lambda: uri)
    try:
        if producer == "link":
            page = mod.Page.__new__(mod.Page)
            page.runtime = runtime
            assert page.acceptNavigationRequest(url, 1, True) is False
        else:
            runtime._new_window(
                SimpleNamespace(requestedUrl=lambda: url, isUserInitiated=lambda: True)
            )
        runtime.emit(p.Started())
    finally:
        output.close()
    assert not output._failed.is_set()
    packets = io.BytesIO(stream.getvalue())
    if not oversized:
        assert p.read_event(packets) == p.OpenExternal(uri)
    assert p.read_event(packets) == p.Started()
    assert p.read_event(packets) is None
