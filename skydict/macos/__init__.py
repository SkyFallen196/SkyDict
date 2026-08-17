"""macOS integration: permissions, global hotkeys."""

from .permissions import (
    PermissionError_,
    check_accessibility,
    check_microphone,
    open_accessibility_settings,
    open_microphone_settings,
    request_accessibility,
)

__all__ = [
    "PermissionError_",
    "check_accessibility",
    "check_microphone",
    "open_accessibility_settings",
    "open_microphone_settings",
    "request_accessibility",
]
