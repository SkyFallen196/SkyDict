"""macOS platform backend: permissions, global hotkeys, clipboard and paste."""

from ..base import HotkeyError, HotkeyEvent, PermissionError_
from .clipboard import Clipboard
from .hotkey import DEFAULT_TRIGGER, MODIFIER_KEYCODES, ModifierHotkeyListener
from .paste import paste_text
from .permissions import (
    check_accessibility,
    check_listen_access,
    check_microphone,
    check_post_access,
    host_process_name,
    open_accessibility_settings,
    open_microphone_settings,
    open_settings_pane,
    request_accessibility,
    require_listen_access,
    require_microphone,
    require_post_access,
    running_in_bundle,
)

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
