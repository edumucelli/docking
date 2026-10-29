"""Owned native confirmation and PIN entry for Windows BLE pairing ceremonies."""

from __future__ import annotations

import ctypes
import struct

from docking.i18n import _
from docking.platform.backends.windows.ffi import bind


def _text(value: str) -> bytes:
    return (value + "\0").encode("utf-16-le")


def _pin_dialog(owner: int) -> str | None:
    user = ctypes.WinDLL("user32", use_last_error=True)
    procedure_type = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
    )
    end = bind(user, "EndDialog", ctypes.c_int32, ctypes.c_void_p, ctypes.c_ssize_t)
    read = bind(
        user,
        "GetDlgItemTextW",
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_int32,
        ctypes.c_wchar_p,
        ctypes.c_int32,
    )
    result: list[str] = []

    def procedure(window, message, wparam, _lparam):
        if message == 0x110:  # WM_INITDIALOG
            bind(
                user,
                "SendDlgItemMessageW",
                ctypes.c_ssize_t,
                ctypes.c_void_p,
                ctypes.c_int32,
                ctypes.c_uint32,
                ctypes.c_size_t,
                ctypes.c_ssize_t,
            )(window, 100, 0xC5, 64, 0)  # EM_SETLIMITTEXT
            return 1
        if message == 0x10:  # WM_CLOSE
            end(window, 2)
            return 1
        if message == 0x111 and wparam & 0xFFFF in (1, 2):
            if wparam & 0xFFFF == 1:
                buffer = ctypes.create_unicode_buffer(65)
                length = read(window, 100, buffer, len(buffer))
                if not length:
                    return 1
                result.append(buffer.value)
                ctypes.memset(ctypes.addressof(buffer), 0, ctypes.sizeof(buffer))
            end(window, wparam & 0xFFFF)
            return 1
        return 0

    # Standard DLGTEMPLATE, using system controls and a native modal loop.
    data = bytearray(struct.pack("<IIHhhhh", 0x80C800C0, 0, 4, 0, 0, 230, 85))
    data += b"\0\0\0\0" + _text(_("Bluetooth"))
    data += struct.pack("<H", 9) + _text("Segoe UI")
    controls = (
        (0x50000000, 8, 8, 214, 17, 101, 0x82, _("Enter PIN")),
        (0x508100A0, 8, 28, 214, 14, 100, 0x81, ""),
        (0x50010001, 111, 58, 52, 16, 1, 0x80, _("OK")),
        (0x50010000, 170, 58, 52, 16, 2, 0x80, _("Cancel")),
    )
    for style, x, y, width, height, identity, kind, title in controls:
        data += bytes((-len(data)) % 4)
        data += struct.pack("<IIhhhhH", style, 0, x, y, width, height, identity)
        data += struct.pack("<HH", 0xFFFF, kind) + _text(title) + b"\0\0"
    template = ctypes.create_string_buffer(bytes(data))
    procedure_callback = procedure_type(procedure)
    status = bind(
        user,
        "DialogBoxIndirectParamW",
        ctypes.c_ssize_t,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        procedure_type,
        ctypes.c_ssize_t,
    )(None, template, owner, procedure_callback, 0)
    if status == -1:
        raise ctypes.WinError(ctypes.get_last_error())
    return result[0] if status == 1 and result else None


def pairing_prompt(owner: int, kind: int, pin: str) -> str | None:
    if kind == 4:  # ProvidePin
        return _pin_dialog(owner)
    if kind == 1:  # ConfirmOnly still gets Windows' system consent dialog.
        return ""
    if kind not in (2, 8):
        return None
    message = bind(
        ctypes.WinDLL("user32", use_last_error=True),
        "MessageBoxW",
        ctypes.c_int32,
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
    )
    text = (
        _("Enter this PIN on the device: {pin}").format(pin=pin)
        if kind == 2
        else _("Does this PIN match the device? {pin}").format(pin=pin)
    )
    # PINs remain inside the helper and native UI; never send them over IPC.
    answer = message(owner, text, _("Bluetooth"), 0x24 if kind == 8 else 0x41)
    return "" if answer == (6 if kind == 8 else 1) else None
