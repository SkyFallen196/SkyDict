"""Synthesise the paste keystroke (Ctrl+V) that drops text into the focused app."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

#: Virtual-key codes. "V" is VK_V on every layout, and Ctrl+V is the universal paste
#: shortcut — the exact analogue of the macOS Cmd+V (physical key 9) implementation.
VK_CONTROL = 0x11
VK_V = 0x56


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT(ctypes.Union):
    # Sized to the largest member (MOUSEINPUT on x64), matching sizeof(INPUT).
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", _INPUT),
    ]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendInput.restype = wintypes.UINT
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]


def _key(vk_code: int, up: bool) -> INPUT:
    event = INPUT()
    event.type = INPUT_KEYBOARD
    event.u.ki.wVk = vk_code
    event.u.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
    return event


def _send_input(events: list[INPUT]) -> None:
    count = len(events)
    array = (INPUT * count)(*events)
    sent = _user32.SendInput(count, array, ctypes.sizeof(INPUT))
    if sent != count:
        # A short count usually means UIPI blocked the events (the focused app runs
        # elevated), or the system rejected them outright.
        raise OSError(
            f"SendInput delivered {sent} of {count} events (Win32 error {ctypes.get_last_error()})"
        )


def paste_text() -> None:
    """Send Ctrl+V. No special permission is required on Windows."""
    _send_input(
        [
            _key(VK_CONTROL, up=False),
            _key(VK_V, up=False),
            _key(VK_V, up=True),
            _key(VK_CONTROL, up=True),
        ]
    )
