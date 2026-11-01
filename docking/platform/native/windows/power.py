"""System power-status ABI shared by Windows applet backends."""

import ctypes


class SystemPowerStatus(ctypes.Structure):
    """SYSTEM_POWER_STATUS uses 32-bit DWORDs on both Windows ABIs."""

    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_uint32),
        ("BatteryFullLifeTime", ctypes.c_uint32),
    ]
