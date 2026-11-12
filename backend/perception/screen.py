"""Screen perception, cheapest first (CLAUDE.md §4.4):

tier 1  foreground window process + title          (Win32 calls, free; polled every couple of seconds)
tier 2  tiny grayscale thumbnail -> difference hash (local, only for the foreground window, memory only)
tier 3  JPEG of the foreground window for the VLM   (only when the scheduler decides to comment)

Privacy (§4.3): tier 3 renders the window's *own* content (PrintWindow), so password-manager popups or
notification toasts layered on top are not captured; the foreground window is re-checked right before and
after; if a window can't render itself (some games), the screen region is used only when nothing
blacklisted overlaps it. Windows only.
"""

import ctypes
import ctypes.wintypes as wt
import io
import logging
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
HASH_SIZE = 16  # 16x16 dHash -> 256 bits
VLM_MAX_SIDE = 1280
VLM_JPEG_QUALITY = 70
# Desktop / taskbar: nothing to comment on.
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
_DIGITS = re.compile(r"\d+")

Blocked = Callable[[str, str], bool]


@dataclass(frozen=True)
class Foreground:
    process: str  # lower-case exe name, e.g. "chrome.exe"; "" if it couldn't be read
    title: str
    pid: int
    rect: tuple[int, int, int, int]  # left, top, right, bottom in physical pixels (visible frame)
    fullscreen: bool
    hwnd: int = field(default=0, compare=False)

    @property
    def key(self) -> str:
        """What counts as "the same content": app + title, digits ignored so a ticking clock, play
        position or "(3) 微信" counter doesn't make every poll look like new content."""
        return f"{self.process}|{_DIGITS.sub('#', self.title)}"


if IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    try:
        _dwmapi = ctypes.WinDLL("dwmapi")
    except OSError:  # pragma: no cover
        _dwmapi = None

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _MONITOR_DEFAULTTONEAREST = 2
    _GW_HWNDPREV = 3
    _DWMWA_EXTENDED_FRAME_BOUNDS = 9
    _DWMWA_CLOAKED = 14
    _PW_RENDERFULLCONTENT = 2
    _BI_RGB = 0
    _DIB_RGB_COLORS = 0
    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
            ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
            ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
            ("biClrImportant", wt.DWORD),
        ]

    def _sig(fn, res, *args):
        fn.restype, fn.argtypes = res, list(args)

    # Explicit signatures: handles are pointer-sized on 64-bit; default c_int could truncate them.
    _sig(_user32.GetForegroundWindow, wt.HWND)
    _sig(_user32.IsIconic, wt.BOOL, wt.HWND)
    _sig(_user32.IsWindowVisible, wt.BOOL, wt.HWND)
    _sig(_user32.GetWindowTextLengthW, ctypes.c_int, wt.HWND)
    _sig(_user32.GetWindowTextW, ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
    _sig(_user32.GetClassNameW, ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
    _sig(_user32.GetWindowThreadProcessId, wt.DWORD, wt.HWND, ctypes.POINTER(wt.DWORD))
    _sig(_user32.GetWindowRect, wt.BOOL, wt.HWND, ctypes.POINTER(wt.RECT))
    _sig(_user32.MonitorFromWindow, wt.HMONITOR, wt.HWND, wt.DWORD)
    _sig(_user32.GetMonitorInfoW, wt.BOOL, wt.HMONITOR, ctypes.POINTER(_MONITORINFO))
    _sig(_user32.GetWindow, wt.HWND, wt.HWND, wt.UINT)
    _sig(_user32.EnumWindows, wt.BOOL, WNDENUMPROC, wt.LPARAM)
    _sig(_user32.GetWindowDC, wt.HDC, wt.HWND)
    _sig(_user32.ReleaseDC, ctypes.c_int, wt.HWND, wt.HDC)
    _sig(_user32.PrintWindow, wt.BOOL, wt.HWND, wt.HDC, wt.UINT)
    _sig(_kernel32.OpenProcess, wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
    _sig(_kernel32.QueryFullProcessImageNameW, wt.BOOL, wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD))
    _sig(_kernel32.CloseHandle, wt.BOOL, wt.HANDLE)
    _sig(_gdi32.CreateCompatibleDC, wt.HDC, wt.HDC)
    _sig(_gdi32.CreateCompatibleBitmap, wt.HBITMAP, wt.HDC, ctypes.c_int, ctypes.c_int)
    _sig(_gdi32.SelectObject, wt.HGDIOBJ, wt.HDC, wt.HGDIOBJ)
    _sig(_gdi32.DeleteObject, wt.BOOL, wt.HGDIOBJ)
    _sig(_gdi32.DeleteDC, wt.BOOL, wt.HDC)
    _sig(
        _gdi32.GetDIBits, ctypes.c_int, wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
        ctypes.POINTER(_BITMAPINFOHEADER), wt.UINT,
    )
    if _dwmapi is not None:
        _sig(_dwmapi.DwmGetWindowAttribute, ctypes.c_long, wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD)

    def _dpi_aware() -> None:
        # Physical pixels everywhere, so window rects match what gets captured on scaled displays.
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001 - older Windows / already set
            try:
                _user32.SetProcessDPIAware()
            except Exception:  # noqa: BLE001
                pass

    _dpi_aware()


def _process_name(pid: int) -> str:
    h = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(len(buf))
        if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        _kernel32.CloseHandle(h)


def _title(hwnd) -> str:
    n = _user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    _user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value.strip()


def _class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _pid(hwnd) -> int:
    pid = wt.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _frame(hwnd) -> tuple[int, int, int, int] | None:
    """Visible frame (without the invisible resize borders GetWindowRect includes)."""
    rect = wt.RECT()
    if _dwmapi is not None and _dwmapi.DwmGetWindowAttribute(
        hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)
    ) == 0:
        return rect.left, rect.top, rect.right, rect.bottom
    if _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return rect.left, rect.top, rect.right, rect.bottom
    return None


def _cloaked(hwnd) -> bool:
    if _dwmapi is None:
        return False
    v = wt.DWORD()
    return _dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(v), ctypes.sizeof(v)) == 0 and v.value != 0


def foreground() -> Foreground | None:
    """Tier 1. None when there is no usable foreground window (desktop, lock screen, minimized)."""
    if not IS_WINDOWS:
        return None
    hwnd = _user32.GetForegroundWindow()
    if not hwnd or _user32.IsIconic(hwnd) or _class(hwnd) in SHELL_CLASSES:
        return None
    r = _frame(hwnd)
    if r is None or r[2] - r[0] < 50 or r[3] - r[1] < 50:
        return None
    mon = _user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    fullscreen = False
    if mon and _user32.GetMonitorInfoW(mon, ctypes.byref(info)):
        m = info.rcMonitor
        fullscreen = r[0] <= m.left and r[1] <= m.top and r[2] >= m.right and r[3] >= m.bottom
    pid = _pid(hwnd)
    return Foreground(
        process=_process_name(pid), title=_title(hwnd), pid=pid, rect=r, fullscreen=fullscreen, hwnd=int(hwnd)
    )


def visible_windows() -> list[tuple[str, str]]:
    """(process, title) of every visible top-level window, minimized ones included — for do-not-disturb:
    OBS or a meeting toolbar matters even when it isn't in front."""
    if not IS_WINDOWS:
        return []
    out: list[tuple[str, str]] = []
    names: dict[int, str] = {}

    @WNDENUMPROC
    def cb(hwnd, _lp):
        if _user32.IsWindowVisible(hwnd) and not _cloaked(hwnd):
            pid = _pid(hwnd)
            if pid not in names:
                names[pid] = _process_name(pid)
            out.append((names[pid], _title(hwnd)))
        return True

    _user32.EnumWindows(cb, 0)
    return out


def _still_foreground(fg: Foreground) -> bool:
    h = _user32.GetForegroundWindow()
    return bool(h) and int(h) == fg.hwnd and _title(h) == fg.title


def _covered_by_blocked(fg: Foreground, blocked: Blocked) -> bool:
    """Is any visible window above fg (z-order) that overlaps it blacklisted (or of unknown process)?"""
    l, t, r, b = fg.rect
    h = _user32.GetWindow(fg.hwnd, _GW_HWNDPREV)
    while h:
        if _user32.IsWindowVisible(h) and not _user32.IsIconic(h) and not _cloaked(h):
            fr = _frame(h)
            if fr and fr[0] < r and fr[2] > l and fr[1] < b and fr[3] > t:
                proc = _process_name(_pid(h))
                if not proc or blocked(proc, _title(h)):
                    return True
        h = _user32.GetWindow(h, _GW_HWNDPREV)
    return False


def _print_window(fg: Foreground) -> np.ndarray | None:
    """BGRA pixels the window renders for itself (unaffected by windows on top); None if unavailable."""
    w0, h0 = fg.rect[2] - fg.rect[0], fg.rect[3] - fg.rect[1]
    full = wt.RECT()
    if not _user32.GetWindowRect(fg.hwnd, ctypes.byref(full)):
        return None
    w, h = full.right - full.left, full.bottom - full.top
    if w <= 0 or h <= 0:
        return None
    wdc = _user32.GetWindowDC(fg.hwnd)
    if not wdc:
        return None
    mdc = _gdi32.CreateCompatibleDC(wdc)
    bmp = _gdi32.CreateCompatibleBitmap(wdc, w, h)
    old = _gdi32.SelectObject(mdc, bmp)
    try:
        if not _user32.PrintWindow(fg.hwnd, mdc, _PW_RENDERFULLCONTENT):
            return None
        bih = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, _BI_RGB, 0, 0, 0, 0, 0)
        buf = (ctypes.c_ubyte * (w * h * 4))()
        if _gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bih), _DIB_RGB_COLORS) != h:
            return None
        px = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
        # Crop the invisible borders so the picture matches the visible frame.
        dx, dy = fg.rect[0] - full.left, fg.rect[1] - full.top
        px = px[max(dy, 0) : max(dy, 0) + h0, max(dx, 0) : max(dx, 0) + w0]
        return px.copy()
    finally:
        _gdi32.SelectObject(mdc, old)
        _gdi32.DeleteObject(bmp)
        _gdi32.DeleteDC(mdc)
        _user32.ReleaseDC(fg.hwnd, wdc)


def _grab(rect: tuple[int, int, int, int]) -> np.ndarray | None:
    """BGRA pixels of a screen rectangle (clamped to the virtual screen)."""
    import mss

    with mss.mss() as sct:
        v = sct.monitors[0]
        left, top = max(rect[0], v["left"]), max(rect[1], v["top"])
        right, bottom = min(rect[2], v["left"] + v["width"]), min(rect[3], v["top"] + v["height"])
        if right - left < 50 or bottom - top < 50:
            return None
        shot = sct.grab({"left": left, "top": top, "width": right - left, "height": bottom - top})
        return np.asarray(shot)


def thumbnail_hash(fg: Foreground) -> np.ndarray | None:
    """Tier 2: 256-bit difference hash of the window region, computed locally and kept in memory only."""
    from PIL import Image

    px = _grab(fg.rect)
    if px is None:
        return None
    img = Image.fromarray(px[:, :, 2::-1]).convert("L").resize((HASH_SIZE + 1, HASH_SIZE), Image.BILINEAR)
    a = np.asarray(img, dtype=np.int16)
    return (a[:, 1:] > a[:, :-1]).flatten()


def hash_distance(a: np.ndarray | None, b: np.ndarray | None) -> float:
    """0 = identical, 1 = completely different; 1 if either is missing."""
    if a is None or b is None or a.shape != b.shape:
        return 1.0
    return float(np.count_nonzero(a != b)) / a.size


def capture_jpeg(fg: Foreground, blocked: Blocked) -> bytes | None:
    """Tier 3: the window as a JPEG for the VLM (never written to disk), or None if it isn't safe now."""
    from PIL import Image

    if not IS_WINDOWS or not fg.hwnd or not _still_foreground(fg):
        return None
    px = _print_window(fg)
    if px is None or px[:, :, :3].mean() < 3:
        # Can't render itself (black: hardware-accelerated / exclusive fullscreen) -> screen region, but only
        # if nothing blacklisted is layered over it.
        if _covered_by_blocked(fg, blocked):
            log.info("窗口上方有隐私窗口，不截图")
            return None
        px = _grab(fg.rect)
    # The user may have switched windows while we were capturing.
    if px is None or not _still_foreground(fg):
        return None
    img = Image.fromarray(px[:, :, 2::-1])
    img.thumbnail((VLM_MAX_SIDE, VLM_MAX_SIDE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=VLM_JPEG_QUALITY)
    return out.getvalue()


def own_process_ids() -> set[int]:
    return {os.getpid()}


OWN_PROCESSES = {"electron.exe"}


def is_own(fg: Foreground) -> bool:
    """The pet's own windows (Electron, or this backend) are never content to comment on."""
    return fg.process in OWN_PROCESSES or fg.pid in own_process_ids()
