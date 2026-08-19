"""Platform integration, dispatched by OS.

Everything above this package — the controller, the inserter, the CLI — talks to the
common interface re-exported here; nothing imports ``platform.macos`` or
``platform.windows`` directly except the tests. Each backend exposes the *same* names,
and the shared types (``HotkeyEvent``, ``PermissionError_``) live in :mod:`.base` so they
are identical objects whichever backend is selected.

The ``if sys.platform`` branches are the one place platform selection happens. py2app's
modulegraph understands ``sys.platform`` comparisons and prunes the other branch, so the
macOS bundle does not try to import the Windows modules (which use ``ctypes.wintypes``).
"""

from __future__ import annotations

import sys

from .base import HotkeyError, HotkeyEvent, PermissionError_

if sys.platform == "darwin":
    from .macos import (  # noqa: F401
        DEFAULT_TRIGGER,
        MODIFIER_KEYCODES,
        Clipboard,
        ModifierHotkeyListener,
        check_accessibility,
        check_listen_access,
        check_microphone,
        check_post_access,
        host_process_name,
        open_accessibility_settings,
        open_microphone_settings,
        open_settings_pane,
        paste_text,
        request_accessibility,
        require_listen_access,
        require_microphone,
        require_post_access,
        running_in_bundle,
    )
elif sys.platform == "win32":
    from .windows import (  # noqa: F401
        DEFAULT_TRIGGER,
        MODIFIER_KEYCODES,
        Clipboard,
        ModifierHotkeyListener,
        check_accessibility,
        check_listen_access,
        check_microphone,
        check_post_access,
        host_process_name,
        open_accessibility_settings,
        open_microphone_settings,
        open_settings_pane,
        paste_text,
        request_accessibility,
        require_listen_access,
        require_microphone,
        require_post_access,
        running_in_bundle,
    )
else:  # pragma: no cover - there is no third backend yet
    raise ImportError(f"SkyDict does not support {sys.platform!r} yet")

__all__ = [
    # Shared types
    "HotkeyError",
    "HotkeyEvent",
    "PermissionError_",
    # Hotkey
    "DEFAULT_TRIGGER",
    "MODIFIER_KEYCODES",
    "ModifierHotkeyListener",
    # Permissions
    "check_listen_access",
    "check_post_access",
    "check_accessibility",
    "check_microphone",
    "request_accessibility",
    "require_listen_access",
    "require_post_access",
    "require_microphone",
    "host_process_name",
    "running_in_bundle",
    "open_settings_pane",
    "open_accessibility_settings",
    "open_microphone_settings",
    # Clipboard and paste
    "Clipboard",
    "paste_text",
]
