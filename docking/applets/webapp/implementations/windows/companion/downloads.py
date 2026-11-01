"""Qt-owned destination dialogs and transfer controls for one browser profile."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEventLoop, QLocale, Qt
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from docking.i18n import _


class Transfer(QDialog):
    def __init__(self, request, owner: Downloads) -> None:
        super().__init__(owner.window)
        self.request, self.owner = request, owner
        self.setWindowTitle(_("Download"))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(440, 150)
        layout = QVBoxLayout(self)
        self.filename = QLabel(request.downloadFileName(), self)
        self.filename.setTextFormat(Qt.TextFormat.PlainText)
        self.filename.setWordWrap(True)
        self.filename.setMaximumWidth(600)
        layout.addWidget(self.filename)
        self.progress = QProgressBar(self)
        layout.addWidget(self.progress)
        self.status = QLabel(self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.pause = QPushButton(self)
        self.pause.clicked.connect(self._toggle)
        buttons.addWidget(self.pause)
        self.cancel = QPushButton(self)
        self.cancel.clicked.connect(self.close)
        buttons.addWidget(self.cancel)
        layout.addLayout(buttons)
        for signal in (
            request.stateChanged,
            request.receivedBytesChanged,
            request.totalBytesChanged,
            request.isPausedChanged,
            request.isFinishedChanged,
        ):
            signal.connect(self.refresh)
        self.finished.connect(self._release)
        self.refresh()

    def refresh(self) -> None:
        request, states = self.request, QWebEngineDownloadRequest.DownloadState
        state, paused, finished = (
            request.state(),
            request.isPaused(),
            request.isFinished(),
        )
        if state == states.DownloadCompleted:
            text = _("Completed")
        elif state == states.DownloadCancelled:
            text = _("Cancelled")
        elif state == states.DownloadInterrupted:
            text = _("Failed") + ": " + request.interruptReasonString()[:512]
        elif paused:
            text = _("Paused")
        else:
            text = _("Downloading")
        received, total = request.receivedBytes(), request.totalBytes()
        size = QLocale().formattedDataSize(received)
        if total >= 0:
            size += " / " + QLocale().formattedDataSize(total)
        self.status.setText(text + " · " + size)
        determinate = total > 0 or state == states.DownloadCompleted
        self.progress.setRange(0, 1000 if determinate else 0)
        if determinate:
            self.progress.setValue(
                1000
                if state == states.DownloadCompleted
                else min(1000, received * 1000 // total)
            )
        self.pause.setText(
            _("Resume") if paused or state == states.DownloadInterrupted else _("Pause")
        )
        self.pause.setEnabled(not finished and state != states.DownloadRequested)
        self.cancel.setText(_("Close") if finished else _("Cancel"))

    def _toggle(self) -> None:
        request = self.request
        if not request.isFinished():
            if (
                request.isPaused()
                or request.state() == request.DownloadState.DownloadInterrupted
            ):
                request.resume()
            else:
                request.pause()

    def _release(self, _result: int) -> None:
        if not self.request.isFinished():
            self.request.cancel()
        self.owner.transfers.remove(self)
        self.request.deleteLater()


class Downloads:
    def __init__(
        self,
        window: QWidget,
        allowed: Callable[[QWebEngineDownloadRequest], bool],
    ) -> None:
        self.window, self.allowed = window, allowed
        self.transfers: list[Transfer] = []
        self.chooser: QFileDialog | None = None
        self._stopped = False

    def request(self, download: QWebEngineDownloadRequest) -> None:
        if (
            self._stopped
            or self.chooser is not None
            or download.isSavePageDownload()
            or not self.allowed(download)
        ):
            download.cancel()
            return
        name = Path(download.suggestedFileName()).name
        if name in {"", ".", ".."}:
            name = "download"
        chooser = QFileDialog(self.window, _("Save"), download.downloadDirectory())
        chooser.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        chooser.setFileMode(QFileDialog.FileMode.AnyFile)
        chooser.selectFile(name)
        chooser.setWindowModality(Qt.WindowModality.WindowModal)
        self.chooser = chooser
        loop, result = QEventLoop(), []

        def finished(value: int) -> None:
            result.append(value)
            loop.quit()

        chooser.finished.connect(finished)
        try:
            # Qt deletes an unaccepted request after downloadRequested returns.
            # Keep that signal frame alive, with an ordinary Qt event loop and
            # a nonblocking native chooser so stop/clear can still reject it.
            chooser.open()
            if not result:
                loop.exec()
            files = chooser.selectedFiles()
            if (
                result == [QDialog.DialogCode.Accepted]
                and files
                and not self._stopped
                and self.allowed(download)
            ):
                destination = Path(files[0])
                download.setDownloadDirectory(str(destination.parent))
                download.setDownloadFileName(destination.name)
                # Retain at most sixteen completed progress windows. Active
                # transfers remain until the user closes them or Docking stops.
                completed = [row for row in self.transfers if row.request.isFinished()]
                for row in completed[:-15]:
                    row.close()
                transfer = Transfer(download, self)
                self.transfers.append(transfer)
                download.accept()
                transfer.show()
            else:
                download.cancel()
        finally:
            self.chooser = None
            chooser.deleteLater()

    def stop(self) -> None:
        self._stopped = True
        if self.chooser is not None:
            self.chooser.reject()
        for transfer in tuple(self.transfers):
            transfer.close()
