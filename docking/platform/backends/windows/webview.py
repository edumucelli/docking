"""Asynchronous WebView2 HWND host, owned by the GTK UI's STA thread.

WebView2 requires its UI thread and message pump for every interface access.
Creation, script execution and profile clearing are callback-based; no browser
operation waits or runs a nested message loop here. Indices/IIDs are the public
Microsoft.Web.WebView2 SDK ABI, not shell-private interfaces.
"""

from __future__ import annotations

import ctypes
import os
import threading
from contextlib import ExitStack
from pathlib import Path

from docking.platform.environment.xdg import docking_data_dir

from .com import GUID, HRESULT, method, object_result, query, release
from .com_callback import ComCallback
from .ffi import bind
from .winrt import check

_P = ctypes.c_void_p
_BOOL = ctypes.c_int32
_TEXT = ctypes.c_wchar_p
_ENVIRONMENT_HANDLER = "4e8a3389-c9d8-4bd2-b6b5-124fee6cc14d"
_CONTROLLER_HANDLER = "6c4819f3-c9b7-4260-8127-c9f5bde7f68c"
_SCRIPT_HANDLER = "b99369f3-9b11-47b5-bc6f-8e7895fcea17"
_EXECUTE_HANDLER = "49511172-cc67-4bca-9923-137112f4c4cc"
_CLEAR_HANDLER = "e9710a06-1d1d-49b2-8234-226f35846ae5"
_CORE13 = GUID.parse("f75f09a8-667e-4983-88d6-c8773f315e84")
_PROFILE2 = GUID.parse("fa740d4b-5eae-4344-a8ad-74be31925397")
_NOTIFICATIONS = GUID.parse("39a7ad55-4287-5cc1-88a1-c6f458593824")
_EVENTS = {
    "navigation_starting": (7, "9adbe429-f36d-432b-9ddc-f8881fbd76e3"),
    "content_loading": (9, "364471e7-f2be-4910-bdba-d72077d51c4b"),
    "navigation_completed": (15, "d33a35bf-1c49-4f98-93ab-006e0533fe1c"),
    "permission": (23, "15e1c6a3-c72a-4df3-91d7-d097fbec6bfd"),
    "process_failed": (25, "79e0aea4-990b-42d9-aa1d-0fcc2e5bc7f1"),
    "message": (34, "57213f19-00e6-49fa-8e07-898ea01ecbd2"),
    "new_window": (44, "d4c185fe-c81c-4989-97af-2d3fa7ab5651"),
    "title": (46, "f5f2b923-953e-4042-9f95-f3a118e1afd4"),
    "window_close": (59, "5c19e9e0-092f-486b-affa-ca8231913039"),
    "notification": (126, "89c5d598-8788-423b-be97-e6e01c0f9ee3"),
}


class Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_int32),
        ("top", ctypes.c_int32),
        ("right", ctypes.c_int32),
        ("bottom", ctypes.c_int32),
    ]


def loader_path() -> Path:
    override = os.environ.get("DOCKING_WEBVIEW2_LOADER")
    return (
        Path(override)
        if override
        else docking_data_dir() / "runtimes" / "webview2" / "WebView2Loader.dll"
    )


class WebView2Host:
    def __init__(
        self, profile: Path, *, on_ready, on_error, loader: Path | None = None
    ):
        self._owner = threading.get_ident()
        self._profile, self._on_ready, self._on_error = profile, on_ready, on_error
        self._scope = ExitStack()
        self._subscriptions: list[tuple[ctypes.c_void_p, int, int]] = []
        self._closed = False
        self._controller, self.core, self.environment = _P(), _P(), _P()
        self._ole = ctypes.WinDLL("ole32", use_last_error=True)
        check(bind(self._ole, "CoInitializeEx", HRESULT, _P, ctypes.c_uint32)(None, 2))
        self._scope.callback(bind(self._ole, "CoUninitialize", None))
        try:
            path = loader if loader is not None else loader_path()
            if not path.is_absolute():
                raise OSError("WebView2 loader path must be absolute")
            self._loader = ctypes.WinDLL(str(path), use_last_error=True)
            version = _P()
            try:
                check(
                    bind(
                        self._loader,
                        "GetAvailableCoreWebView2BrowserVersionString",
                        HRESULT,
                        _TEXT,
                        ctypes.POINTER(_P),
                    )(None, ctypes.byref(version))
                )
                if not version.value:
                    raise OSError("WebView2 Runtime is unavailable")
            finally:
                if version.value:
                    bind(self._ole, "CoTaskMemFree", None, _P)(version)
        except OSError:
            self._scope.close()
            raise

    def _assert_owner(self) -> None:
        if threading.get_ident() != self._owner:
            raise RuntimeError("WebView2 accessed outside its owning STA thread")

    def _delegate(self, iid, arguments, callback):
        return ComCallback(iid, arguments, callback, on_error=self._failed)

    def _failed(self, error: Exception) -> None:
        if not self._closed:
            self._on_error(error)

    def _retain(self, pointer):
        if not pointer:
            raise OSError("Missing WebView2 interface")
        result = _P(pointer)
        method(result, 1, ctypes.c_uint32)(result)
        self._scope.callback(release, result)
        return result

    def start(self, parent: int) -> None:
        self._assert_owner()
        self._parent = parent
        handler = self._delegate(
            _ENVIRONMENT_HANDLER, (HRESULT, _P), self._environment_ready
        )
        try:
            check(
                bind(
                    self._loader,
                    "CreateCoreWebView2EnvironmentWithOptions",
                    HRESULT,
                    _TEXT,
                    _TEXT,
                    _P,
                    _P,
                )(None, str(self._profile), None, handler.pointer)
            )
        finally:
            handler.close()

    def _environment_ready(self, status, pointer):
        if self._closed:
            return
        check(status)
        self.environment = self._retain(pointer)
        handler = self._delegate(
            _CONTROLLER_HANDLER, (HRESULT, _P), self._controller_ready
        )
        try:
            check(
                method(self.environment, 3, HRESULT, _P, _P)(
                    self.environment, self._parent, handler.pointer
                )
            )
        finally:
            handler.close()

    def _controller_ready(self, status, pointer):
        if self._closed:
            if pointer:
                method(_P(pointer), 24, HRESULT)(_P(pointer))
            return
        check(status)
        self._controller = self._retain(pointer)
        self.core = object_result(self._scope, self._controller, 25)
        self.resize()
        self._on_ready()

    def on(self, event: str, callback) -> None:
        self._assert_owner()
        index, iid = _EVENTS[event]
        pointer = (
            query(self._scope, self.core, _NOTIFICATIONS)
            if event == "notification"
            else self.core
        )
        self.subscribe(pointer, index, iid, callback)

    def on_accelerator(self, callback) -> None:
        self._assert_owner()
        self.subscribe(
            self._controller, 19, "b29c7e28-fa79-41a8-8e44-65811c76dcb2", callback
        )

    def subscribe(self, pointer, index, iid, callback) -> None:
        handler = self._delegate(
            iid, (_P, _P), lambda _sender, args: callback(_P(args))
        )
        token = ctypes.c_int64()
        try:
            check(
                method(pointer, index, HRESULT, _P, ctypes.POINTER(ctypes.c_int64))(
                    pointer, handler.pointer, ctypes.byref(token)
                )
            )
            self._subscriptions.append((pointer, index + 1, token.value))
        finally:
            handler.close()

    def text(self, pointer, index: int, *, limit: int = 32768) -> str:
        self._assert_owner()
        result = _P()
        try:
            check(
                method(pointer, index, HRESULT, ctypes.POINTER(_P))(
                    pointer, ctypes.byref(result)
                )
            )
            if not result.value:
                return ""
            length = bind(
                ctypes.CDLL("ucrtbase"), "wcsnlen", ctypes.c_size_t, _P, ctypes.c_size_t
            )(result, limit + 1)
            if length > limit:
                raise OSError("WebView2 text exceeded its limit")
            return ctypes.wstring_at(result, length)
        finally:
            if result.value:
                bind(self._ole, "CoTaskMemFree", None, _P)(result)

    def put(self, pointer, index: int, enabled: int) -> None:
        self._assert_owner()
        check(method(pointer, index, HRESULT, _BOOL)(pointer, enabled))

    def script(self, source: str, on_complete) -> None:
        self._assert_owner()

        def complete(status, identifier):
            if not self._closed:
                check(status)
                on_complete(identifier)

        handler = self._delegate(_SCRIPT_HANDLER, (HRESULT, _TEXT), complete)
        try:
            check(
                method(self.core, 27, HRESULT, _TEXT, _P)(
                    self.core, source, handler.pointer
                )
            )
        finally:
            handler.close()

    def execute(self, source: str, on_complete) -> None:
        self._assert_owner()

        def complete(status, text):
            if not self._closed:
                check(status)
                on_complete(text)

        handler = self._delegate(_EXECUTE_HANDLER, (HRESULT, _TEXT), complete)
        try:
            check(
                method(self.core, 29, HRESULT, _TEXT, _P)(
                    self.core, source, handler.pointer
                )
            )
        finally:
            handler.close()

    def navigate(self, uri: str) -> None:
        self._assert_owner()
        check(method(self.core, 5, HRESULT, _TEXT)(self.core, uri))

    def reload(self) -> None:
        self._assert_owner()
        check(method(self.core, 31, HRESULT)(self.core))

    def clear(self, on_complete) -> None:
        self._assert_owner()
        with ExitStack() as scope:
            view = query(scope, self.core, _CORE13)
            profile = object_result(scope, view, 105)
            clear = query(scope, profile, _PROFILE2)
            handler = self._delegate(
                _CLEAR_HANDLER,
                (HRESULT,),
                lambda status: on_complete(status) if not self._closed else None,
            )
            try:
                check(method(clear, 12, HRESULT, _P)(clear, handler.pointer))
            finally:
                handler.close()

    def resize(self) -> None:
        self._assert_owner()
        if self._controller.value:
            rectangle = Rect()
            if not bind(
                ctypes.WinDLL("user32"),
                "GetClientRect",
                _BOOL,
                _P,
                ctypes.POINTER(Rect),
            )(self._parent, ctypes.byref(rectangle)):
                raise ctypes.WinError(ctypes.get_last_error())
            check(
                method(self._controller, 6, HRESULT, Rect)(self._controller, rectangle)
            )

    def show(self, visible: bool) -> None:
        self._assert_owner()
        if self._controller.value:
            self.put(self._controller, 4, visible)
            if visible:
                check(
                    method(self._controller, 12, HRESULT, ctypes.c_int32)(
                        self._controller, 0
                    )
                )

    def close(self) -> None:
        self._assert_owner()
        if self._closed:
            return
        self._closed = True
        try:
            for pointer, index, token in reversed(self._subscriptions):
                try:
                    check(
                        method(pointer, index, HRESULT, ctypes.c_int64)(pointer, token)
                    )
                except OSError:
                    # A crashed browser can reject removals; close still owns
                    # and releases the complete controller/interface graph.
                    continue
            if self._controller.value:
                check(method(self._controller, 24, HRESULT)(self._controller))
        finally:
            self._subscriptions.clear()
            self._scope.close()
            self._controller, self.core, self.environment = _P(), _P(), _P()
