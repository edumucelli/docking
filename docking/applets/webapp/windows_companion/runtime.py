"""Qt browser process. No GTK objects or notification text cross its pipes."""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
from collections.abc import Callable
from typing import Any, TypeVar, cast

from PySide6.QtCore import QFile, QIODevice, QLockFile, QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEnginePermission,
    QWebEngineProfile,
    QWebEngineProfileBuilder,
    QWebEngineScript,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QToolBar,
)

from docking.i18n import _

from ..bridge import parse_bridge_message
from ..state import NavigationTarget, navigation_target
from .downloads import Downloads
from .input import UnicodePackets
from .notifications import DesktopNotifier
from .protocol import Configuration, encode, read

_F = TypeVar("_F", bound=Callable[..., object])


def _slot(*types: type) -> Callable[[_F], _F]:
    # Qt's C-extension decorator preserves the Python signature. Keep this
    # boundary typed even where the optional companion runtime is not installed.
    def decorate(function: _F) -> _F:
        return cast(_F, Slot(*types)(function))

    return decorate


class Dispatch(QObject):
    invoke = Signal(object)

    @_slot(object)
    def execute(self, callback) -> None:
        callback()


class StatusBridge(QObject):
    def __init__(self, receive: Callable[[str], None], parent: QObject) -> None:
        super().__init__(parent)
        self._receive = receive

    @_slot(str)
    def post(self, raw: str) -> None:
        self._receive(raw)


class BrowserWindow(QMainWindow):
    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self.setWindowFlag(Qt.WindowType.Tool, True)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt virtual method.
        event.ignore()
        self.hide()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt virtual method.
        super().showEvent(event)
        self.runtime.emit("visible", value=True)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt virtual method.
        super().hideEvent(event)
        self.runtime.emit("visible", value=False)


class Page(QWebEnginePage):
    def __init__(self, runtime, profile, parent) -> None:
        super().__init__(profile, parent)
        self.runtime = runtime

    def acceptNavigationRequest(self, url, navigation_type, main_frame) -> bool:  # noqa: N802 - Qt virtual method.
        if not main_frame:
            return True
        uri = url.toString()
        target = navigation_target(uri, self.runtime.configuration.profile.host)
        if target is NavigationTarget.INTERNAL:
            return True
        if (
            target is NavigationTarget.EXTERNAL
            and navigation_type
            == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
        ):
            self.runtime.emit("external", uri=uri)
        return False

    def javaScriptConsoleMessage(self, _level, _message, _line, _source) -> None:  # noqa: N802 - Qt virtual method.
        # Console output can contain account data. It is never an IPC channel.
        pass


class Runtime:
    def __init__(
        self, configuration: Configuration, send: Callable[[dict[str, Any]], bool]
    ) -> None:
        self.configuration, self._send = configuration, send
        self.dispatch = Dispatch()
        self.dispatch.invoke.connect(
            self.dispatch.execute, Qt.ConnectionType.QueuedConnection
        )
        self._closing = self._clearing = self._disposed = False
        self._has_status = False
        self._last: dict[str, Any] = {}
        self._lock = QLockFile(str(configuration.directory.with_suffix(".lock")))
        if not self._lock.tryLock(0):
            raise OSError("Browser profile is already in use")
        self._build()

    def post(self, callback: Callable[[], None]) -> None:
        self.dispatch.invoke.emit(callback)

    def emit(self, event: str, **values) -> None:
        packet = {"event": event, **values}
        if event in {"title", "visible", "bridge"} and self._last.get(event) == values:
            return
        self._last[event] = values
        if not self._send(packet):
            self.stop()

    def _build(self) -> None:
        self._disposed = False
        self._last.clear()
        service = self.configuration.profile
        directory = self.configuration.directory
        builder = self._profile_builder()
        builder.setPersistentStoragePath(str(directory / "storage"))
        builder.setCachePath(str(directory / "cache"))
        builder.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        # Device consent is requested again after each launch; notification
        # permission alone follows the explicitly selected service profile.
        builder.setPersistentPermissionsPolicy(
            QWebEngineProfile.PersistentPermissionsPolicy.StoreInMemory
        )
        self.profile = builder.createProfile(
            service.session_name, QApplication.instance()
        )
        if self.profile is None:
            raise OSError("Browser profile is already in use")
        self.window = BrowserWindow(self)
        self.window.setWindowTitle(service.name)
        self.view = QWebEngineView(self.window)
        self.page = Page(self, self.profile, self.view)
        self.view.setPage(self.page)
        self._unicode_packets = UnicodePackets(self.view)
        self.window.setCentralWidget(self.view)
        toolbar = QToolBar(self.window)
        toolbar.setMovable(False)
        toolbar.addAction(
            _("Reload {service}").format(service=service.name), self.view.reload
        )
        self.window.addToolBar(toolbar)
        self._escape = QShortcut(QKeySequence("Escape"), self.window)
        self._escape.activated.connect(self.window.hide)
        bounds = QApplication.primaryScreen().availableGeometry()
        self.window.resize(
            max(1, min(1100, bounds.width() - 32)),
            max(1, min(720, bounds.height() - 80)),
        )
        self.window.move(bounds.left() + 16, bounds.top() + 8)
        self.notifier = DesktopNotifier(
            service,
            directory,
            post=self.post,
            on_activate=self._activate,
            on_shown=lambda: self.emit("notification"),
        )
        self.profile.setNotificationPresenter(self.notifier.show)
        self.downloads = Downloads(self.window, self._download_allowed)
        self.profile.downloadRequested.connect(self.downloads.request)
        self.page.permissionRequested.connect(self._permission)
        self.page.newWindowRequested.connect(self._new_window)
        self.page.windowCloseRequested.connect(self.window.hide)
        self.page.titleChanged.connect(
            lambda title: self.emit("title", value=title[:4096])
        )
        self.page.loadStarted.connect(self._load_started)
        self.page.loadFinished.connect(self._load_finished)
        self.page.renderProcessTerminated.connect(
            lambda *_args: self.emit("phase", value="error")
        )
        self._install_bridge()
        self.emit("started")
        self.page.setUrl(QUrl(service.url))

    def _profile_builder(self):
        return QWebEngineProfileBuilder()

    def _install_bridge(self) -> None:
        service = self.configuration.profile
        self._channel = QWebChannel(self.page)
        self._status = StatusBridge(self._bridge, self._channel)
        self._channel.registerObject("status", self._status)
        self.page.setWebChannel(
            self._channel, QWebEngineScript.ScriptWorldId.ApplicationWorld
        )
        resource = QFile(":/qtwebchannel/qwebchannel.js")
        if not resource.open(QIODevice.OpenModeFlag.ReadOnly):
            raise OSError("Browser status channel is unavailable")
        library = bytes(resource.readAll()).decode()
        resource.close()
        guard = (
            "if(window===window.top && location.origin==="
            + json.dumps(service.origin.rstrip("/"))
            + "){"
        )
        isolated = (
            library
            + "\n"
            + guard
            + """
            let status=null;const pending=[];
            window.addEventListener('docking-webapp-status',event=>{
                if(typeof event.detail!=='string'||event.detail.length>4096)return;
                if(status)status.post(event.detail);
                else {if(pending.length===16)pending.shift();pending.push(event.detail)}
            });
            new QWebChannel(qt.webChannelTransport,channel=>{
                status=channel.objects.status;
                for(const raw of pending)status.post(raw);
                pending.length=0;
            });
        }"""
        )
        main = (
            guard
            + """
            Object.defineProperty(window,'__dockingBrowserBridge',{
                value:Object.freeze({postMessage:raw=>window.dispatchEvent(
                    new CustomEvent('docking-webapp-status',{detail:raw}))})});
        """
            + service.bridge_script
            + "\n}"
        )
        for name, source, world in (
            (
                "Docking isolated status",
                isolated,
                QWebEngineScript.ScriptWorldId.ApplicationWorld,
            ),
            (
                "Docking public browser APIs",
                main,
                QWebEngineScript.ScriptWorldId.MainWorld,
            ),
        ):
            script = QWebEngineScript()
            script.setName(name)
            script.setWorldId(world)
            script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            script.setRunsOnSubFrames(False)
            script.setSourceCode(source)
            self.page.scripts().insert(script)

    def _bridge(self, raw: str) -> None:
        if (
            self._closing
            or self._clearing
            or not self.configuration.profile.allows_origin(self.page.url().toString())
        ):
            return
        event = parse_bridge_message(raw)
        if event is not None:
            if event.kind == "status":
                self._has_status = True
            self.emit("bridge", value=raw)

    def _load_started(self) -> None:
        self._has_status = False
        self.emit("phase", value="starting")

    def _load_finished(self, ok: bool) -> None:
        if not self._closing and not self._clearing:
            if not ok:
                self.emit("phase", value="error")
            elif not self._has_status:
                self.emit("phase", value="syncing")

    def _activate(self) -> None:
        if not self._closing and not self._clearing:
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()
            self.emit("attention")

    def _new_window(self, request) -> None:
        uri = request.requestedUrl().toString()
        if (
            request.isUserInitiated()
            and navigation_target(uri, self.configuration.profile.host)
            is NavigationTarget.EXTERNAL
        ):
            self.emit("external", uri=uri)

    def _permission(self, request) -> None:
        if (
            self._closing
            or self._clearing
            or not self.configuration.profile.allows_origin(request.origin().toString())
        ):
            request.deny()
            return
        kind = request.permissionType()
        types = QWebEnginePermission.PermissionType
        if kind == types.Notifications:
            request.grant()
            return
        devices = []
        if kind in {types.MediaAudioCapture, types.MediaAudioVideoCapture}:
            devices.append(_("microphone"))
        if kind in {types.MediaVideoCapture, types.MediaAudioVideoCapture}:
            devices.append(_("camera"))
        allowed = bool(devices) and self.window.isVisible()
        for device in devices:
            if not allowed:
                break
            allowed = (
                QMessageBox.question(
                    self.window,
                    self.configuration.profile.name,
                    _("Allow {service} to use your {device}?").format(
                        service=self.configuration.profile.name, device=device
                    ),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                == QMessageBox.StandardButton.Yes
            )
        if (
            allowed
            and request.isValid()
            and not self._closing
            and self.configuration.profile.allows_origin(self.page.url().toString())
        ):
            request.grant()
        else:
            request.deny()

    def _download_allowed(self, download) -> bool:
        return bool(
            not self._closing
            and not self._clearing
            and self.window.isVisible()
            and download.page() is self.page
            and self.configuration.profile.allows_origin(self.page.url().toString())
        )

    def command(self, packet: dict[str, Any]) -> None:
        operation = packet.get("op")
        if operation == "stop":
            self.stop()
        elif not self._closing and not self._clearing:
            if operation == "show":
                self._activate()
            elif operation == "hide":
                self.window.hide()
            elif operation == "reload":
                self.page.triggerAction(QWebEnginePage.WebAction.Reload)
            elif (
                operation == "clear"
                and type(packet.get("id")) is int
                and 0 < packet["id"] < 2**31
            ):
                self._clearing = True
                self.emit("restart", id=packet["id"])
                self.stop()

    def _after_notifications(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._unicode_packets.close()
        self.window.hide()
        self.profile.destroyed.connect(self._profile_destroyed)
        self.window.destroyed.connect(self.profile.deleteLater)
        self.window.deleteLater()

    def _profile_destroyed(self) -> None:
        QApplication.instance().quit()

    def stop(self) -> None:
        if self._closing:
            return
        self._closing = True
        if not self._disposed:
            self.downloads.stop()
            self.notifier.stop(self._after_notifications)


class Output:
    def __init__(self) -> None:
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(128)
        self._failed = threading.Event()
        self._thread = threading.Thread(
            target=self._write, name="webapp-events", daemon=False
        )
        self._thread.start()

    def send(self, packet: dict[str, Any]) -> bool:
        if self._failed.is_set():
            return False
        try:
            self._queue.put_nowait(packet)
        except queue.Full:
            return False
        return True

    def _write(self) -> None:
        try:
            while (packet := self._queue.get()) is not None:
                sys.stdout.buffer.write(encode(packet))
                sys.stdout.buffer.flush()
        except (OSError, ValueError):
            self._failed.set()

    def close(self) -> None:
        while self._thread.is_alive():
            try:
                self._queue.put(None, timeout=0.1)
                break
            except queue.Full:
                continue
        self._thread.join()


def main(runtime_type=Runtime) -> None:
    packet = read(sys.stdin.buffer)
    if packet is None:
        return
    configuration = Configuration.parse(packet)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Docking")
    output = Output()
    runtime = None
    try:
        runtime = runtime_type(configuration, output.send)

        def commands() -> None:
            # A raw duplicate avoids holding sys.stdin's buffered lock in a
            # daemon reader when the GUI exits independently of pipe EOF.
            stream = os.fdopen(os.dup(sys.stdin.fileno()), "rb", buffering=0)
            try:
                while packet := read(stream):
                    runtime.post(lambda packet=packet: runtime.command(packet))
            except (OSError, ValueError):
                pass
            finally:
                stream.close()
                runtime.post(runtime.stop)

        threading.Thread(target=commands, name="webapp-input", daemon=True).start()
        app.exec()
    finally:
        if runtime is not None:
            runtime._lock.unlock()
        output.close()
