"""Worker-owned WinRT toast publication and unpackaged COM activation.

Only immutable event names and opaque tokens leave this MTA boundary. Browser
interfaces remain on their owning STA. Each browser profile has a stable user
identity, preserving native preferences.
Shutdown removes owned toasts and revokes the live COM server. A profile mutex
prevents another process from replacing that active route.
"""

from __future__ import annotations

import ctypes
import hashlib
import uuid
from contextlib import ExitStack, suppress
from xml.sax.saxutils import escape

from .com import GUID, HRESULT, method, object_result, query, value
from .com_callback import ComCallback, ComObject
from .ffi import bind
from .winrt import WindowsRuntime, check

_P = ctypes.c_void_p
_ACTIVATION_FACTORY = GUID.parse("00000035-0000-0000-c000-000000000046")
_XML_IO = GUID.parse("6cd0e74e-ee65-4489-9ebf-ca43e87ba637")
_MANAGER = GUID.parse("50ac103f-d235-4598-bbef-98fe4d1a3ad4")
_HISTORY = GUID.parse("7ab93c52-0e48-4750-ba9d-1a4113981847")
_TOAST_FACTORY = GUID.parse("04124b20-82c6-4229-b109-fd9ed4662b53")
_TOAST2 = GUID.parse("9dfb9fd1-143a-490e-90bf-b9fba7132de7")
_CLASS_FACTORY = "00000001-0000-0000-c000-000000000046"
_ACTIVATION_CALLBACK = "53e31837-6600-4a81-9395-75cffe746f94"
_GROUP = "Docking.WebApp"


def toast_xml(token: str, title: str, body: str, silent: bool) -> str:
    def text(raw: str, limit: int) -> str:
        # XML 1.0 excludes NUL/control characters and lone UTF-16 surrogates.
        return escape(
            "".join(
                c
                for c in raw[:limit]
                if c in "\t\n\r"
                or " " <= c <= "\ud7ff"
                or "\ue000" <= c <= "\ufffd"
                or "\U00010000" <= c <= "\U0010ffff"
            )
        )

    return (
        '<toast launch="' + escape(token, {'"': "&quot;"}) + '">'
        '<visual><binding template="ToastGeneric"><text>'
        + text(title, 256)
        + "</text><text>"
        + text(body, 2048)
        + "</text></binding></visual>"
        + ('<audio silent="true"/>' if silent else "")
        + "</toast>"
    )


class ToastPublisher:
    """Enter, use and close on one serial worker; callbacks may arrive by RPC."""

    def __init__(self, service_name: str, profile_namespace: str, on_event) -> None:
        digest = hashlib.sha256(profile_namespace.casefold().encode()).hexdigest()[:32]
        self.identity = "org.docking.Docking.WebApp." + digest
        self._class_id = GUID.parse(str(uuid.uuid5(uuid.NAMESPACE_URL, self.identity)))
        self._service_name, self._on_event = service_name, on_event
        self._scope: ExitStack | None = None
        self._runtime: WindowsRuntime | None = None
        self._toasts: dict[str, tuple[ExitStack, ctypes.c_void_p, str]] = {}
        self._notifier, self._history, self._factory = _P(), _P(), _P()

    def _ensure(self) -> None:
        if self._scope is not None:
            return
        scope = ExitStack()
        try:
            runtime = scope.enter_context(WindowsRuntime())
            self._register(scope)
            manager = runtime.factory(
                scope, "Windows.UI.Notifications.ToastNotificationManager", _MANAGER
            )
            with runtime.string(self.identity) as identity:
                self._notifier = object_result(scope, manager, 7, _P, identity)
            history_manager = runtime.factory(
                scope, "Windows.UI.Notifications.ToastNotificationManager", _HISTORY
            )
            self._history = object_result(scope, history_manager, 6)
            self._factory = runtime.factory(
                scope, "Windows.UI.Notifications.ToastNotification", _TOAST_FACTORY
            )
            self._runtime, self._scope = runtime, scope
            self._clear_history()
        except OSError:
            self._scope = self._runtime = None
            scope.close()
            raise

    def _register(self, scope: ExitStack) -> None:
        import winreg

        # Only one live publisher may own this profile's COM route. The stable
        # application identity preserves Windows preferences across restarts.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = bind(kernel, "CreateMutexW", _P, _P, ctypes.c_int32, ctypes.c_wchar_p)(
            None,
            True,
            "Local\\Docking.WebApp.Toast." + self.identity.rsplit(".", 1)[-1],
        )
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        close_handle = bind(kernel, "CloseHandle", ctypes.c_int32, _P)
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS.
            close_handle(handle)
            raise OSError("Browser notification profile already active")
        scope.callback(close_handle, handle)
        scope.callback(bind(kernel, "ReleaseMutex", ctypes.c_int32, _P), handle)
        class_text = "{" + str(uuid.UUID(bytes_le=bytes(self._class_id))) + "}"
        path = "Software\\Classes\\AppUserModelId\\" + self.identity
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
            winreg.SetValueEx(
                key, "DisplayName", 0, winreg.REG_SZ, self._service_name + " in Docking"
            )
            winreg.SetValueEx(key, "CustomActivator", 0, winreg.REG_SZ, class_text)

        # Keep the stable app registration and user notification settings.
        # Runtime shutdown revokes only our COM class and removes owned toasts.

        def create(outer, iid, output):
            if not output or not iid:
                return -2147467261
            output[0] = None
            if outer:
                return -2147221232  # CLASS_E_NOAGGREGATION.
            activation = ComCallback(
                _ACTIVATION_CALLBACK,
                (ctypes.c_wchar_p, ctypes.c_wchar_p, _P, ctypes.c_uint32),
                lambda identity, token, _inputs, _count: (
                    self._on_event("clicked", token)
                    if identity == self.identity
                    else None
                ),
                on_error=lambda _error: None,
                agile=True,
            )
            try:
                return activation._query(None, iid, output)
            finally:
                activation.close()

        factory = ComObject(
            _CLASS_FACTORY,
            (
                ((_P, ctypes.POINTER(GUID), ctypes.POINTER(_P)), create),
                ((ctypes.c_int32,), lambda _lock: 0),
            ),
            on_error=lambda _error: None,
            agile=True,
        )
        scope.callback(factory.close)
        ole = ctypes.WinDLL("ole32", use_last_error=True)
        cookie = ctypes.c_uint32()
        check(
            bind(
                ole,
                "CoRegisterClassObject",
                HRESULT,
                ctypes.POINTER(GUID),
                _P,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_uint32),
            )(ctypes.byref(self._class_id), factory.pointer, 4, 1, ctypes.byref(cookie))
        )
        scope.callback(
            bind(ole, "CoRevokeClassObject", HRESULT, ctypes.c_uint32), cookie.value
        )

    def _subscribe(self, scope: ExitStack, toast, index, iid, callback) -> None:
        handler = ComCallback(
            iid, (_P, _P), callback, on_error=lambda _error: None, agile=True
        )
        token = ctypes.c_int64()
        try:
            check(
                method(toast, index, HRESULT, _P, ctypes.POINTER(ctypes.c_int64))(
                    toast, handler.pointer, ctypes.byref(token)
                )
            )
        finally:
            handler.close()

        def remove() -> None:
            with suppress(OSError):
                check(
                    method(toast, index + 1, HRESULT, ctypes.c_int64)(
                        toast, token.value
                    )
                )

        scope.callback(remove)

    def show(
        self,
        token: str,
        title: str,
        body: str,
        silent: bool,
        replaces: str | None = None,
    ) -> bool:
        self._ensure()
        runtime = self._runtime
        assert runtime is not None
        # Show applies native user/policy settings. get_Setting can return
        # ERROR_NOT_FOUND before Windows has seen this new owned identity.
        scope = ExitStack()
        previous = self._toasts.get(replaces) if replaces is not None else None
        tag = previous[2] if previous is not None else token
        try:
            activation = runtime.factory(
                scope, "Windows.Data.Xml.Dom.XmlDocument", _ACTIVATION_FACTORY
            )
            xml = object_result(scope, activation, 6)
            io = query(scope, xml, _XML_IO)
            with runtime.string(toast_xml(token, title, body, silent)) as text:
                check(method(io, 6, HRESULT, _P)(io, text))
            toast = object_result(scope, self._factory, 6, _P, xml)
            properties = query(scope, toast, _TOAST2)
            for index, text in ((6, tag), (8, _GROUP)):
                with runtime.string(text) as value_text:
                    check(
                        method(properties, index, HRESULT, _P)(properties, value_text)
                    )
            self._subscribe(
                scope,
                toast,
                11,
                "ab54de2d-97d9-5528-b6ad-105afe156530",
                lambda *_args: self._on_event("clicked", token),
            )
            self._subscribe(
                scope,
                toast,
                9,
                "61c2402f-0ed0-5a18-ab69-59f4aa99a368",
                lambda _sender, args: (
                    self._on_event("closed", token)
                    if value(_P(args), 6, ctypes.c_int32).value != 2
                    else None
                ),
            )
            self._subscribe(
                scope,
                toast,
                13,
                "95e3e803-c969-5e3a-9753-ea2ad22a9a33",
                lambda *_args: self._on_event("closed", token),
            )
            self._toasts[token] = (scope, toast, tag)
            check(method(self._notifier, 6, HRESULT, _P)(self._notifier, toast))
        except OSError:
            self._toasts.pop(token, None)
            scope.close()
            if replaces is not None:
                self.hide(replaces)
            raise
        if replaces is not None:
            old = self._toasts.pop(replaces, None)
            if old is not None:
                old[0].close()
        return True

    def hide(self, token: str) -> None:
        entry = self._toasts.pop(token, None)
        if entry is None:
            return
        scope, toast, native_tag = entry
        try:
            assert self._runtime is not None
            try:
                check(method(self._notifier, 7, HRESULT, _P)(self._notifier, toast))
            finally:
                with (
                    self._runtime.string(native_tag) as tag,
                    self._runtime.string(_GROUP) as group,
                    self._runtime.string(self.identity) as identity,
                ):
                    check(
                        method(self._history, 8, HRESULT, _P, _P, _P)(
                            self._history, tag, group, identity
                        )
                    )
                if not self._toasts:
                    self._clear_history()
        finally:
            scope.close()

    def _clear_history(self) -> None:
        # This profile's mutex makes the application identity exclusive. Clear
        # only that identity, including stale records left by a crashed host.
        assert self._runtime is not None
        with self._runtime.string(self.identity) as identity:
            check(method(self._history, 12, HRESULT, _P)(self._history, identity))

    def close(self) -> None:
        try:
            for token in tuple(self._toasts):
                with suppress(OSError):
                    self.hide(token)
        finally:
            if self._scope is not None:
                assert self._runtime is not None
                with suppress(OSError):
                    self._clear_history()
                self._scope.close()
                self._scope = self._runtime = None
