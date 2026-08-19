"""Tests for the Windows platform backend: hotkey hook logic, clipboard, paste.

These run only on Windows — the modules under test use ``ctypes.wintypes``, which does not
exist elsewhere. The clipboard round-trip is the one test that touches the real clipboard;
it snapshots first and restores in a ``finally`` so nothing is left behind.
"""

from __future__ import annotations

import ctypes

import pytest

pytest.importorskip("ctypes.wintypes", reason="Windows-only platform backend")

from skydict.platform.base import HotkeyEvent  # noqa: E402
from skydict.platform.windows import clipboard as wclip  # noqa: E402
from skydict.platform.windows import hotkey as whotkey  # noqa: E402
from skydict.platform.windows import paste as wpaste  # noqa: E402

# --------------------------------------------------------------------------- hotkey


@pytest.fixture
def no_next_hook(monkeypatch):
    """Stop _handle from calling into the real CallNextHookEx with a NULL handle."""
    monkeypatch.setattr(whotkey._user32, "CallNextHookEx", lambda *args: 0)


#: Keeps the structs built by :func:`key_event` alive. The address alone does not pin the
#: ctypes object, so letting it be collected would leave _handle reading freed memory.
_KEEPALIVE: list[whotkey.KBDLLHOOKSTRUCT] = []


def key_event(vk: int) -> int:
    """Build a KBDLLHOOKSTRUCT in memory and return its address as an LPARAM."""
    struct = whotkey.KBDLLHOOKSTRUCT()
    struct.vkCode = vk
    _KEEPALIVE.append(struct)
    return ctypes.addressof(struct)


@pytest.fixture
def listener():
    seen: list[HotkeyEvent] = []
    instance = whotkey.ModifierHotkeyListener("right_alt", on_event=seen.append)
    instance.seen = seen
    return instance


def test_press_and_release_are_reported(no_next_hook, listener):
    listener._handle(0, whotkey.WM_SYSKEYDOWN, key_event(whotkey.MODIFIER_KEYCODES["right_alt"]))
    listener._handle(0, whotkey.WM_SYSKEYUP, key_event(whotkey.MODIFIER_KEYCODES["right_alt"]))

    assert listener.seen == [HotkeyEvent.PRESSED, HotkeyEvent.RELEASED]
    assert not listener.is_held


def test_control_triggers_use_keydown_not_syskeydown(no_next_hook):
    seen: list[HotkeyEvent] = []
    listener = whotkey.ModifierHotkeyListener("right_control", on_event=seen.append)

    listener._handle(0, whotkey.WM_KEYDOWN, key_event(whotkey.MODIFIER_KEYCODES["right_control"]))
    listener._handle(0, whotkey.WM_KEYUP, key_event(whotkey.MODIFIER_KEYCODES["right_control"]))

    assert seen == [HotkeyEvent.PRESSED, HotkeyEvent.RELEASED]


def test_other_modifier_keys_are_ignored(no_next_hook, listener):
    listener._handle(0, whotkey.WM_KEYDOWN, key_event(whotkey.MODIFIER_KEYCODES["left_alt"]))
    listener._handle(0, whotkey.WM_KEYUP, key_event(whotkey.MODIFIER_KEYCODES["left_alt"]))

    assert listener.seen == []


def test_autorepeat_press_events_emit_once(no_next_hook, listener):
    vk = whotkey.MODIFIER_KEYCODES["right_alt"]
    for _ in range(3):
        listener._handle(0, whotkey.WM_SYSKEYDOWN, key_event(vk))

    assert listener.seen == [HotkeyEvent.PRESSED]


def test_release_without_press_is_ignored(no_next_hook, listener):
    listener._handle(0, whotkey.WM_SYSKEYUP, key_event(whotkey.MODIFIER_KEYCODES["right_alt"]))

    assert listener.seen == []


def test_handler_exception_does_not_break_the_hook(no_next_hook):
    def explode(_event):
        raise RuntimeError("handler crashed")

    listener = whotkey.ModifierHotkeyListener("right_alt", on_event=explode)

    listener._handle(0, whotkey.WM_SYSKEYDOWN, key_event(whotkey.MODIFIER_KEYCODES["right_alt"]))

    assert listener.is_held  # state still advanced despite the failure


def test_unknown_trigger_is_rejected():
    with pytest.raises(ValueError, match="Unknown trigger"):
        whotkey.ModifierHotkeyListener("right_option")  # macOS name, not Windows


def test_default_trigger_is_a_known_key():
    assert whotkey.DEFAULT_TRIGGER in whotkey.MODIFIER_KEYCODES


# ------------------------------------------------------------------------ clipboard


def test_format_names_round_trip(monkeypatch):
    assert wclip._format_name(wclip.CF_UNICODETEXT) == "CF_UNICODETEXT"
    assert wclip._format_id("CF_UNICODETEXT") == wclip.CF_UNICODETEXT
    assert wclip._format_id("49171") == 49171

    monkeypatch.setattr(wclip._user32, "RegisterClipboardFormatW", lambda name: 0xC000)
    assert wclip._format_id("my.private.format") == 0xC000


def test_restore_places_locale_first(monkeypatch):
    """CF_LOCALE must be on the clipboard before any other format."""
    ordered: list[str] = []
    clipboard = wclip.Clipboard()

    monkeypatch.setattr(wclip._user32, "OpenClipboard", lambda _: True)
    monkeypatch.setattr(wclip._user32, "CloseClipboard", lambda: True)
    monkeypatch.setattr(wclip._user32, "EmptyClipboard", lambda: True)
    monkeypatch.setattr(
        clipboard,
        "_set_data",
        lambda fmt, data: ordered.append(wclip._format_name(fmt)),
    )

    clipboard.restore([{"CF_TEXT": b"a", "CF_LOCALE": b"l", "CF_UNICODETEXT": b"u"}])

    assert ordered[0] == "CF_LOCALE"


def test_text_round_trip_preserves_the_clipboard():
    """The one test against the real clipboard: write, read back, restore the original."""
    clipboard = wclip.Clipboard()
    original = clipboard.snapshot()
    try:
        clipboard.write_text("продиктованный текст")

        assert clipboard.read_text() == "продиктованный текст"
    finally:
        clipboard.restore(original)


def test_empty_clipboard_reads_empty_string():
    clipboard = wclip.Clipboard()
    original = clipboard.snapshot()
    try:
        clipboard.write_text("")  # clears it

        assert clipboard.read_text() == ""
    finally:
        clipboard.restore(original)


# ----------------------------------------------------------------------------- paste


def test_paste_sends_ctrl_v(monkeypatch):
    captured: list[list[tuple[int, bool]]] = []

    def fake_send_input(count, array, _size):
        captured.append(
            [
                (array[i].u.ki.wVk, bool(array[i].u.ki.dwFlags & wpaste.KEYEVENTF_KEYUP))
                for i in range(count)
            ]
        )
        return count

    monkeypatch.setattr(wpaste._user32, "SendInput", fake_send_input)

    wpaste.paste_text()

    assert captured == [
        [
            (wpaste.VK_CONTROL, False),
            (wpaste.VK_V, False),
            (wpaste.VK_V, True),
            (wpaste.VK_CONTROL, True),
        ]
    ]


def test_paste_raises_when_events_are_dropped(monkeypatch):
    monkeypatch.setattr(wpaste._user32, "SendInput", lambda count, array, size: 0)

    with pytest.raises(OSError, match="SendInput"):
        wpaste.paste_text()
