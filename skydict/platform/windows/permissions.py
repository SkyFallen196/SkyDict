"""Windows "permissions".

Windows has no Accessibility grant and no per-app API for classic desktop apps:

* **Hotkey** — a low-level keyboard hook needs no permission at all.
* **Paste** — ``SendInput`` needs no permission (except into elevated apps, which UIPI
  blocks regardless; that surfaces as a paste failure, not a permission toggle).
* **Microphone** — governed by Settings › Privacy & Security › Microphone's
  "let desktop apps access your microphone" toggle. There is no supported programmatic
  query for classic apps, so the checks below are best-effort: opening the input stream
  is what actually surfaces a denial.

So most functions are honest no-ops that report "granted"; only the microphone check
attempts a real answer by reading the privacy consent store.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

log = logging.getLogger(__name__)

_MICROPHONE_SETTINGS_URI = "ms-settings:privacy-microphone"
_PRIVACY_SETTINGS_URI = "ms-settings:privacy"

#: Registry key holding the Win10/11 "let desktop apps access the microphone" toggle.
_CONSENT_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
    r"\ConsentStore\microphone"
)


def running_in_bundle() -> bool:
    """Whether this process is inside a frozen (PyInstaller/cx_Freeze) executable."""
    return bool(getattr(sys, "frozen", False))


def host_process_name() -> str:
    return sys.executable


def check_listen_access() -> bool:
    """Global keyboard hooks need no permission on Windows."""
    return True


def check_post_access() -> bool:
    """SendInput needs no permission on Windows."""
    return True


def check_accessibility() -> bool:
    return True


def request_accessibility() -> bool:
    """Nothing to request."""
    return True


def require_listen_access() -> None:
    return None


def require_post_access() -> None:
    return None


def require_microphone() -> None:
    # The recorder's attempt to open the stream is what raises a readable error.
    return None


def check_microphone() -> str:
    """Best-effort read of the desktop-app microphone consent store.

    Returns ``authorized``, ``denied`` or ``unknown``. Falls back to ``unknown`` on any
    failure — the registry layout is undocumented and varies by build.
    """
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CONSENT_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "Value")
        return {"Allow": "authorized", "Deny": "denied"}.get(value, "unknown")
    except OSError as exc:
        log.debug("Could not read the microphone consent store: %s", exc)
        return "unknown"
    except ImportError:  # pragma: no cover - winreg is stdlib on Windows
        return "unknown"


def open_settings_pane(pane: str | None = None) -> None:
    """Open a Windows Settings page.

    macOS carries its settings URL in ``pane``; on Windows we map it to the closest
    equivalent (microphone privacy) and otherwise open the general privacy page.
    """
    uri = _MICROPHONE_SETTINGS_URI if pane and "Microphone" in pane else _PRIVACY_SETTINGS_URI
    try:
        os.startfile(uri)  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover - os.startfile is Windows-only
        subprocess.run(["start", uri], check=False, shell=True)


def open_accessibility_settings() -> None:
    open_settings_pane()


def open_microphone_settings() -> None:
    open_settings_pane(_MICROPHONE_SETTINGS_URI)
