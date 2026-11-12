"""Is a *declared* hotkey combo still held down? (push-to-talk release detection)

Electron's globalShortcut only reports presses. To notice the release, the backend polls the state of the
keys of that one combo from config/hotkeys.yaml while it is held. Nothing else is looked at, nothing is
stored or logged (CLAUDE.md §4.2: only declared combos, keystrokes are never recorded).
"""

import ctypes
import re
import sys

# Each modifier is a group of alternatives (left/right keys).
_MODIFIERS: dict[str, tuple[int, ...]] = {
    "Ctrl": (0x11,),
    "Alt": (0x12,),
    "Shift": (0x10,),
    "Super": (0x5B, 0x5C),
}
_NAMED: dict[str, int] = {
    "Space": 0x20, "Tab": 0x09, "Enter": 0x0D, "Backspace": 0x08, "Delete": 0x2E, "Insert": 0x2D,
    "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22,
    "Up": 0x26, "Down": 0x28, "Left": 0x25, "Right": 0x27, "Esc": 0x1B,
    "numadd": 0x6B, "numsub": 0x6D, "nummult": 0x6A, "numdiv": 0x6F, "numdec": 0x6E,
    "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
    "\\": 0xDC, "`": 0xC0,
}  # fmt: skip

Combo = list[tuple[int, ...]]


def _key_vk(key: str) -> int | None:
    if re.fullmatch(r"[A-Z0-9]", key):
        return ord(key)
    m = re.fullmatch(r"F([1-9]|1[0-9]|2[0-4])", key)
    if m:
        return 0x70 + int(m.group(1)) - 1
    m = re.fullmatch(r"num([0-9])", key)
    if m:
        return 0x60 + int(m.group(1))
    return _NAMED.get(key)


def combo_keys(accelerator: str) -> Combo | None:
    """Virtual-key groups for a canonical accelerator ("Ctrl+Alt+H", "F9"); None if it can't be mapped."""
    groups: Combo = []
    parts = [p.strip() for p in accelerator.split("+")]
    if not parts or any(not p for p in parts):
        return None
    for p in parts[:-1]:
        mod = _MODIFIERS.get(p)
        if mod is None:
            return None
        groups.append(mod)
    vk = _key_vk(parts[-1])
    if vk is None:
        return None
    groups.append((vk,))
    return groups


def combo_down(combo: Combo) -> bool:
    """True while every key of the combo is physically held. Always False off Windows."""
    if sys.platform != "win32":
        return False
    state = ctypes.windll.user32.GetAsyncKeyState
    return all(any(state(vk) & 0x8000 for vk in group) for group in combo)
