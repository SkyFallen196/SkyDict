"""Types shared by every platform backend.

Both the macOS and Windows implementations import these, so ``HotkeyEvent`` and
``PermissionError_`` are the *same* objects whichever backend the dispatcher picks —
comparisons and ``except`` clauses written against them stay correct.
"""

from __future__ import annotations

from enum import Enum


class HotkeyEvent(Enum):
    PRESSED = "pressed"
    RELEASED = "released"


class HotkeyError(RuntimeError):
    """The global hotkey hook could not be installed or was disabled by the system."""


class PermissionError_(RuntimeError):
    """A required OS permission has not been granted.

    The trailing underscore avoids shadowing the builtin :class:`PermissionError`.
    ``pane`` carries an OS-specific settings URL on platforms that have one (macOS);
    it is None elsewhere.
    """

    def __init__(self, message: str, pane: str | None = None) -> None:
        super().__init__(message)
        self.pane = pane
