"""Synthesise the paste keystroke (Cmd+V) that drops text into the focused app."""

from __future__ import annotations

#: Virtual keycode for "v" on every layout — CGEvent keycodes are physical, not logical,
#: so this works regardless of the user's keyboard layout.
KEYCODE_V = 9


def paste_text() -> None:
    """Send Cmd+V through the HID event tap.

    Needs Accessibility ("post event access"), checked by the caller.
    """
    import Quartz

    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    key_down = Quartz.CGEventCreateKeyboardEvent(source, KEYCODE_V, True)
    key_up = Quartz.CGEventCreateKeyboardEvent(source, KEYCODE_V, False)

    # Set the Command flag explicitly rather than synthesising a Cmd key press: this
    # cannot leave a modifier stuck down if the process dies mid-paste.
    Quartz.CGEventSetFlags(key_down, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventSetFlags(key_up, Quartz.kCGEventFlagMaskCommand)

    Quartz.CGEventPost(Quartz.kCGHIDEventTap, key_down)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, key_up)
