"""Delivering recognised text to whatever app has focus.

Two strategies:

* **paste** — put the text on the clipboard, synthesise the platform paste keystroke
  (Cmd+V on macOS, Ctrl+V on Windows), then put the user's own clipboard back. Needs
  Accessibility on macOS; needs nothing on Windows.
* **clipboard_only** — leave the text on the clipboard for the user to paste. Needs no
  permission, and is the fallback when Accessibility is missing.

Typing the text character by character was considered and rejected: it is slow for a
paragraph of dictation, and apps with autocomplete mangle synthetic keystrokes.

The platform-specific parts (the clipboard and the paste keystroke) live in
:mod:`skydict.platform`; this module is the OS-agnostic wrapper around them.
"""

from __future__ import annotations

import logging
import time

from ..config import InsertMode
from ..platform import Clipboard, PermissionError_, check_post_access, paste_text

log = logging.getLogger(__name__)

#: The focused app reads the clipboard asynchronously after the paste keystroke, so it
#: has to stay ours briefly. Restoring too early makes the paste land on stale content.
PASTE_SETTLE_SECONDS = 0.15


class TextInserter:
    """Pastes text into the focused application via a synthetic paste keystroke."""

    mode: InsertMode = "paste"

    def __init__(
        self,
        clipboard: Clipboard | None = None,
        restore_clipboard: bool = True,
        settle: float = PASTE_SETTLE_SECONDS,
    ) -> None:
        self.clipboard = clipboard or Clipboard()
        self.restore_clipboard = restore_clipboard
        self.settle = settle

    def deliver(self, text: str) -> None:
        if not text:
            return
        if not check_post_access():
            raise PermissionError_(
                "Cannot paste without Accessibility permission. "
                "Set insert_mode to 'clipboard_only' to deliver text without it."
            )

        previous = self.clipboard.snapshot() if self.restore_clipboard else None
        self.clipboard.write_text(text)
        try:
            self._press_paste()
            time.sleep(self.settle)
        finally:
            if previous is not None:
                self.clipboard.restore(previous)

    def _press_paste(self) -> None:
        paste_text()


class ClipboardInserter:
    """Leaves the text on the clipboard without touching the focused app."""

    mode: InsertMode = "clipboard_only"

    def __init__(self, clipboard: Clipboard | None = None) -> None:
        self.clipboard = clipboard or Clipboard()

    def deliver(self, text: str) -> None:
        if text:
            self.clipboard.write_text(text)


def build_inserter(mode: InsertMode, fallback: bool = True) -> TextInserter | ClipboardInserter:
    """Build the configured inserter.

    With ``fallback`` a paste request degrades to clipboard-only when the platform cannot
    paste, so dictation still produces something usable instead of failing outright.
    """
    if mode == "clipboard_only":
        return ClipboardInserter()
    if mode != "paste":
        raise ValueError(f"Unknown insert mode: {mode!r}")

    if fallback and not check_post_access():
        log.warning(
            "Paste permission is missing; falling back to clipboard-only delivery"
        )
        return ClipboardInserter()
    return TextInserter()
