"""Global hotkey handling through a low-level keyboard hook (``WH_KEYBOARD_LL``).

``RegisterHotKey`` cannot express "hold a modifier alone", so the hook is the Windows
analogue of the macOS ``CGEventTap``: it observes every keystroke system-wide and reports
press/release of one chosen modifier. Left and right modifiers are distinct virtual-key
codes (``VK_LMENU`` vs ``VK_RMENU``), so the physical key is known for free — no IOKit
bit-mask gymnastics needed.

The hook is listen-only: events are passed on with ``CallNextHookEx`` untouched, so
SkyDict never eats a keystroke another app needed.

Holding the trigger key alone should be inert. Right Alt is the direct analogue of the
macOS right-Option default, but on layouts where right Alt is AltGr (Russian, most
European layouts) it can type characters; pick ``right_control`` or ``right_shift`` there.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from collections.abc import Callable
from ctypes import wintypes

from ..base import HotkeyError, HotkeyEvent

log = logging.getLogger(__name__)

#: Virtual-key codes for the modifier keys usable as a trigger. The Win keys are omitted:
#: holding one alone opens the Start menu, which a listen-only hook cannot prevent.
MODIFIER_KEYCODES: dict[str, int] = {
    "left_control": 0xA2,  # VK_LCONTROL
    "right_control": 0xA3,  # VK_RCONTROL
    "left_shift": 0xA0,  # VK_LSHIFT
    "right_shift": 0xA1,  # VK_RSHIFT
    "left_alt": 0xA4,  # VK_LMENU
    "right_alt": 0xA5,  # VK_RMENU
}

DEFAULT_TRIGGER = "right_alt"

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012

#: Pointer-sized Win32 integer types.
LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_size_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SetWindowsHookExW.restype = ctypes.c_void_p
_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL
_user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
_user32.CallNextHookEx.restype = LRESULT
_user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, WPARAM, LPARAM]
_user32.GetMessageW.restype = wintypes.BOOL
_user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
_user32.PeekMessageW.restype = wintypes.BOOL
_user32.PeekMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
    wintypes.UINT,
]
_user32.TranslateMessage.restype = wintypes.BOOL
_user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.DispatchMessageW.restype = LRESULT
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.PostThreadMessageW.restype = wintypes.BOOL
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, WPARAM, LPARAM]


class ModifierHotkeyListener:
    """Watches one modifier key and reports press and release.

    Installs the hook and runs a message loop on a dedicated thread, mirroring the macOS
    listener's ``CFRunLoop`` thread: it composes with any future tray/UI event loop as
    well as with a plain CLI process.
    """

    def __init__(
        self,
        trigger: str = DEFAULT_TRIGGER,
        on_event: Callable[[HotkeyEvent], None] | None = None,
    ) -> None:
        if trigger not in MODIFIER_KEYCODES:
            raise ValueError(
                f"Unknown trigger {trigger!r}. Choose one of: {', '.join(MODIFIER_KEYCODES)}"
            )
        self.trigger = trigger
        self.keycode = MODIFIER_KEYCODES[trigger]
        self.on_event = on_event

        self._hook = None
        self._hook_proc = None
        self._thread: threading.Thread | None = None
        self._held = False
        self._ready = threading.Event()
        self._error: Exception | None = None

    @property
    def is_held(self) -> bool:
        return self._held

    def _handle(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code >= 0:
            vk_code = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents.vkCode
            if vk_code == self.keycode:
                if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    if not self._held:
                        self._held = True
                        self._emit(HotkeyEvent.PRESSED)
                elif w_param in (WM_KEYUP, WM_SYSKEYUP):
                    if self._held:
                        self._held = False
                        self._emit(HotkeyEvent.RELEASED)

        return _user32.CallNextHookEx(self._hook, n_code, w_param, l_param)

    def _emit(self, event: HotkeyEvent) -> None:
        log.debug("Hotkey %s: %s", self.trigger, event.value)
        if self.on_event is None:
            return
        try:
            self.on_event(event)
        except Exception as exc:  # a handler crash must not kill the hook
            log.exception("Hotkey handler failed: %s", exc)

    def start(self) -> None:
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, name="skydict-hotkey", daemon=True)
        self._thread.start()

        if not self._ready.wait(timeout=5.0):
            raise HotkeyError("Timed out while starting the hotkey listener")
        if self._error is not None:
            raise self._error

    def _run(self) -> None:
        # Keep the callback alive: a GC'd CFUNCTYPE would crash the next keystroke.
        self._hook_proc = HOOKPROC(self._handle)
        msg = wintypes.MSG()

        try:
            self._hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_proc, None, 0)
            if not self._hook:
                raise HotkeyError(
                    f"Could not install the keyboard hook (Win32 error {ctypes.get_last_error()})"
                )
            # Force the thread's message queue to exist before start() returns, so an
            # immediate stop() can PostThreadMessageW to it.
            _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
            log.info("Listening for the %s key", self.trigger)
        except Exception as exc:
            self._error = exc
            self._ready.set()
            return

        self._ready.set()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

        _user32.UnhookWindowsHookEx(self._hook)
        self._hook = None

    def stop(self) -> None:
        if self._thread is not None:
            _user32.PostThreadMessageW(self._thread.ident, WM_QUIT, 0, 0)
            self._thread.join(timeout=2.0)
            self._thread = None
        self._hook = None
        self._held = False

    def __enter__(self) -> ModifierHotkeyListener:
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()
