"""Clipboard access through the Win32 clipboard API, with save/restore around a paste.

``snapshot``/``restore`` preserve the *memory-backed* formats — plain text, unicode text,
RTF/HTML and any registered or private format stored as an ``HGLOBAL``. Handle-backed
formats (``CF_BITMAP``, ``CF_ENHMETAFILE``, ``CF_METAFILEPICT``, ``CF_PALETTE`` and file
drops ``CF_HDROP``) are not yet captured; a paste over one of those loses it. That is a
known stage-1 gap, not a silent bug: they are skipped with a debug log.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

log = logging.getLogger(__name__)

CF_TEXT = 1
CF_BITMAP = 2
CF_METAFILEPICT = 3
CF_SYLK = 4
CF_DIF = 5
CF_TIFF = 6
CF_OEMTEXT = 7
CF_DIB = 8
CF_PALETTE = 9
CF_PENDATA = 10
CF_RIFF = 11
CF_WAVE = 12
CF_UNICODETEXT = 13
CF_ENHMETAFILE = 14
CF_HDROP = 15
CF_LOCALE = 16
CF_DIBV5 = 17

#: Formats whose GetClipboardData result is a GDI/handle object rather than an HGLOBAL
#: memory block, plus the owner-drawn/display formats that exist only as delayed renders.
SKIP_FORMATS = frozenset(
    {
        CF_BITMAP,
        CF_METAFILEPICT,
        CF_PALETTE,
        CF_ENHMETAFILE,
        CF_HDROP,
        0x0080,  # CF_OWNERDISPLAY
        0x0081,  # CF_DSPTEXT
        0x0082,  # CF_DSPBITMAP
        0x0083,  # CF_DSPMETAFILEPICT
        0x008E,  # CF_DSPENHMETAFILE
    }
)

#: Standard format id -> the name used as a key in snapshots.
FORMAT_NAMES: dict[int, str] = {
    CF_TEXT: "CF_TEXT",
    CF_OEMTEXT: "CF_OEMTEXT",
    CF_UNICODETEXT: "CF_UNICODETEXT",
    CF_SYLK: "CF_SYLK",
    CF_DIF: "CF_DIF",
    CF_TIFF: "CF_TIFF",
    CF_RIFF: "CF_RIFF",
    CF_WAVE: "CF_WAVE",
    CF_DIB: "CF_DIB",
    CF_DIBV5: "CF_DIBV5",
    CF_PENDATA: "CF_PENDATA",
    CF_LOCALE: "CF_LOCALE",
}

GMEM_MOVEABLE = 0x0002

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.OpenClipboard.restype = wintypes.BOOL
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.GetClipboardData.restype = wintypes.HANDLE
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.SetClipboardData.restype = wintypes.HANDLE
_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_user32.EnumClipboardFormats.restype = wintypes.UINT
_user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
_user32.GetClipboardFormatNameW.restype = ctypes.c_int
_user32.GetClipboardFormatNameW.argtypes = [wintypes.UINT, wintypes.LPWSTR, ctypes.c_int]
_user32.RegisterClipboardFormatW.restype = wintypes.UINT
_user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]

_kernel32.GlobalAlloc.restype = wintypes.HANDLE
_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
_kernel32.GlobalUnlock.restype = wintypes.BOOL
_kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
_kernel32.GlobalSize.restype = ctypes.c_size_t
_kernel32.GlobalSize.argtypes = [wintypes.HANDLE]
_kernel32.GlobalFree.restype = wintypes.HANDLE
_kernel32.GlobalFree.argtypes = [wintypes.HANDLE]


def _open(timeout: float = 0.5) -> None:
    """Open the clipboard, retrying while another process holds it."""
    deadline = time.monotonic() + timeout
    while not _user32.OpenClipboard(None):
        if time.monotonic() >= deadline:
            raise OSError(f"Could not open the clipboard (Win32 error {ctypes.get_last_error()})")
        time.sleep(0.01)


def _read_global(handle: int) -> bytes:
    size = _kernel32.GlobalSize(handle)
    if not size:
        return b""
    ptr = _kernel32.GlobalLock(handle)
    try:
        return ctypes.string_at(ptr, size)
    finally:
        _kernel32.GlobalUnlock(handle)


def _format_name(fmt: int) -> str:
    if fmt in FORMAT_NAMES:
        return FORMAT_NAMES[fmt]
    buffer = ctypes.create_unicode_buffer(256)
    if _user32.GetClipboardFormatNameW(fmt, buffer, 256):
        return buffer.value
    return str(fmt)


def _format_id(name: str) -> int:
    if name in FORMAT_NAMES.values():
        for fmt, known in FORMAT_NAMES.items():
            if known == name:
                return fmt
    if name.isdigit():
        return int(name)
    return _user32.RegisterClipboardFormatW(name)


class Clipboard:
    """Reads and writes the Windows clipboard.

    Snapshots capture every *memory-backed* format on the clipboard, not just plain text,
    so restoring after a paste gives back text, RTF/HTML and registered formats intact.
    """

    def read_text(self) -> str | None:
        _open()
        try:
            if not _user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return None
            handle = _user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            ptr = _kernel32.GlobalLock(handle)
            try:
                return ctypes.cast(ptr, ctypes.c_wchar_p).value
            finally:
                _kernel32.GlobalUnlock(handle)
        finally:
            _user32.CloseClipboard()

    def write_text(self, text: str) -> None:
        payload = text.encode("utf-16-le") + b"\x00\x00"
        _open()
        try:
            if not _user32.EmptyClipboard():
                raise OSError(
                    f"Could not clear the clipboard (Win32 error {ctypes.get_last_error()})"
                )
            self._set_data(CF_UNICODETEXT, payload)
        finally:
            _user32.CloseClipboard()

    def snapshot(self) -> list[dict[str, bytes]]:
        """Capture the current memory-backed formats. An empty clipboard yields ``[]``."""
        _open()
        try:
            representations: dict[str, bytes] = {}
            fmt = 0
            while True:
                fmt = _user32.EnumClipboardFormats(fmt)
                if not fmt:
                    break
                if fmt in SKIP_FORMATS:
                    log.debug("Skipping handle-backed clipboard format %d", fmt)
                    continue
                handle = _user32.GetClipboardData(fmt)
                if not handle:
                    continue
                representations[_format_name(fmt)] = _read_global(handle)
            return [representations] if representations else []
        finally:
            _user32.CloseClipboard()

    def restore(self, snapshot: list[dict[str, bytes]]) -> None:
        """Put a snapshot back. An empty snapshot clears the clipboard."""
        _open()
        try:
            if not _user32.EmptyClipboard():
                log.warning("Could not clear the clipboard for restore")
                return
            if not snapshot:
                return
            # CF_LOCALE must be placed on the clipboard before any other format.
            ordered = sorted(
                snapshot[0].items(),
                key=lambda item: 0 if item[0] == "CF_LOCALE" else 1,
            )
            for name, data in ordered:
                try:
                    self._set_data(_format_id(name), data)
                except OSError as exc:  # one bad format must not kill the rest
                    log.warning("Could not restore clipboard format %s: %s", name, exc)
        finally:
            _user32.CloseClipboard()

    @staticmethod
    def _set_data(fmt: int, data: bytes) -> None:
        handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, max(len(data), 1))
        if not handle:
            raise OSError(f"GlobalAlloc failed (Win32 error {ctypes.get_last_error()})")
        ptr = _kernel32.GlobalLock(handle)
        try:
            ctypes.memmove(ptr, data, len(data))
        finally:
            _kernel32.GlobalUnlock(handle)

        if not _user32.SetClipboardData(fmt, handle):
            _kernel32.GlobalFree(handle)
            raise OSError(f"SetClipboardData failed (Win32 error {ctypes.get_last_error()})")
