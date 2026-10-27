"""Route Windows Unicode packets through one ordered Qt text input path."""

from ctypes.wintypes import MSG

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication
from PySide6.QtGui import QGuiApplication, QInputMethodEvent
from PySide6.QtWidgets import QApplication

from .text_input import PacketText


class UnicodePackets(QAbstractNativeEventFilter):
    def __init__(self, view) -> None:
        super().__init__()
        self._view = view
        self._window = None
        self._receiver = None
        self._text = PacketText()
        QApplication.instance().installNativeEventFilter(self)

    def nativeEventFilter(self, event_type, address):  # noqa: N802 - Qt virtual method.
        if event_type != b"windows_generic_MSG":
            return False, 0
        message = MSG.from_address(int(address))
        widget, receiver = QApplication.focusWidget(), QGuiApplication.focusObject()
        if (
            widget is None
            or receiver is None
            or not (widget is self._view or self._view.isAncestorOf(widget))
        ):
            self._window = self._receiver = None
            self._text.reset()
            return False, 0
        if receiver is not self._receiver:
            self._receiver = receiver
            self._text.reset()
        if message.message == 0x100:  # WM_KEYDOWN.
            self._window = message.hWnd if message.wParam == 0xE7 else None
        elif message.message == 0x101 and message.wParam == 0xE7:
            return True, 0  # No unmatched VK_PACKET key-up enters Chromium.
        elif message.message == 0x102 and message.hWnd == self._window:
            # Qt peeks WM_CHAR inside its key mapper, then invokes this native
            # filter before queuing a BMP key or directly committing a surrogate
            # pair. Consume both forms here, preserving the original text order.
            text = self._text.feed(int(message.wParam))
            if text:
                commit = QInputMethodEvent()
                commit.setCommitString(text)
                QCoreApplication.sendEvent(receiver, commit)
            return True, 0
        return False, 0

    def close(self) -> None:
        QApplication.instance().removeNativeEventFilter(self)
        self._text.reset()
