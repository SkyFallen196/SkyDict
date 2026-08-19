"""macOS privacy permissions.

Three checks matter:

* **Microphone** — to record at all.
* **Listen event access** — for the CGEventTap that watches for the hotkey.
* **Post event access** — to send the Cmd+V keystroke that pastes into another app.

The last two are both governed by the Accessibility entry in System Settings, but Quartz
reports them separately, and asking the specific question gives a far better error
message than a blanket "Accessibility is off". Clipboard-only delivery needs neither.

In development the permission belongs to whatever binary launched the process — Terminal,
iTerm or the IDE — not to SkyDict, because an unbundled Python script has no identity of
its own. A packaged .app gets its own entry.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

from ..base import PermissionError_

log = logging.getLogger(__name__)

ACCESSIBILITY_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
MICROPHONE_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"


def running_in_bundle() -> bool:
    """Whether this process is inside a packaged .app.

    py2app sets this in the bundle's boot script; it is the difference between "enable
    SkyDict" and "enable your terminal" in the instructions below.
    """
    return "RESOURCEPATH" in os.environ or ".app/Contents/" in sys.executable


def host_process_name() -> str:
    """Full path of the binary the permission is attached to.

    The bare name is useless in the System Settings list — every conda environment has a
    binary called "python" — so the full path is what the user needs to see.
    """
    return sys.executable


def _quartz():
    try:
        import Quartz
    except ImportError:  # pragma: no cover - pyobjc is a macOS-only extra
        log.warning("pyobjc is not installed; cannot check event permissions")
        return None
    return Quartz


def check_listen_access() -> bool:
    """Whether this process may observe keyboard events — needed for the hotkey tap."""
    quartz = _quartz()
    return bool(quartz.CGPreflightListenEventAccess()) if quartz else False


def check_post_access() -> bool:
    """Whether this process may synthesise keystrokes — needed to paste."""
    quartz = _quartz()
    return bool(quartz.CGPreflightPostEventAccess()) if quartz else False


def check_accessibility() -> bool:
    """Whether both halves of Accessibility are available."""
    return check_listen_access() and check_post_access()


def request_accessibility() -> bool:
    """Ask macOS for event access, showing its dialog.

    The prompt appears only the first time a given binary asks; after that macOS
    remembers the answer and the call returns silently, so a user who declined must
    re-enable the entry in System Settings by hand.
    """
    quartz = _quartz()
    if quartz is None:
        return False
    listening = bool(quartz.CGRequestListenEventAccess())
    posting = bool(quartz.CGRequestPostEventAccess())
    return listening and posting


def _accessibility_message(what: str) -> str:
    if running_in_bundle():
        who = "Enable SkyDict in the list."
    else:
        who = (
            "Running from a terminal, the entry to enable is the terminal app itself "
            f"rather than the interpreter ({host_process_name()}) — macOS attributes "
            "the permission to the app that launched the process."
        )
    return (
        f"Accessibility permission is required to {what}.\n"
        f"Grant it in System Settings › Privacy & Security › Accessibility. {who}\n"
        "Then restart SkyDict."
    )


def require_listen_access() -> None:
    if check_listen_access():
        return
    raise PermissionError_(
        _accessibility_message("watch for the global hotkey"), pane=ACCESSIBILITY_PANE
    )


def require_post_access() -> None:
    if check_post_access():
        return
    raise PermissionError_(
        _accessibility_message("paste into other apps")
        + "\nAlternatively set insert_mode to 'clipboard_only', which needs no permission.",
        pane=ACCESSIBILITY_PANE,
    )


def check_microphone() -> str:
    """Return the microphone authorisation status.

    One of ``authorized``, ``denied``, ``restricted``, ``not_determined`` or ``unknown``.
    """
    try:
        import AVFoundation
    except ImportError:  # pragma: no cover - pyobjc is a macOS-only extra
        log.warning("pyobjc is not installed; cannot check microphone access")
        return "unknown"

    status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
        AVFoundation.AVMediaTypeAudio
    )
    return {
        0: "not_determined",
        1: "restricted",
        2: "denied",
        3: "authorized",
    }.get(status, "unknown")


def require_microphone() -> None:
    status = check_microphone()
    # "not_determined" is fine: opening the input stream triggers the system prompt.
    if status in {"authorized", "not_determined", "unknown"}:
        return
    raise PermissionError_(
        f"Microphone access is {status}.\n"
        f"Grant it to '{host_process_name()}' in System Settings › Privacy & Security › "
        "Microphone.",
        pane=MICROPHONE_PANE,
    )


def open_settings_pane(pane: str) -> None:
    """Open a System Settings privacy pane by URL."""
    subprocess.run(["open", pane], check=False)


def open_accessibility_settings() -> None:
    open_settings_pane(ACCESSIBILITY_PANE)


def open_microphone_settings() -> None:
    open_settings_pane(MICROPHONE_PANE)
