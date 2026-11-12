"""How long since the last keyboard or mouse input, from GetLastInputInfo.

Only the timestamp is read (milliseconds since boot) — never which keys, never the content.
"""

import ctypes
from ctypes import wintypes

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds() -> float | None:
    """Seconds since the user last pressed a key or moved the mouse; None if it can't be read."""
    info = _LASTINPUTINFO(cbSize=ctypes.sizeof(_LASTINPUTINFO))
    if not _user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    elapsed_ms = (_kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
    return elapsed_ms / 1000
