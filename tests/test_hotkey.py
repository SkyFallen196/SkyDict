"""Hotkey tests against a fake Quartz module — no event tap, no permissions needed."""

from __future__ import annotations

import sys
import types

import pytest

from skydict.macos.hotkey import (
    DEVICE_FLAG_MASKS,
    MODIFIER_KEYCODES,
    HotkeyEvent,
    ModifierHotkeyListener,
)


@pytest.fixture
def fake_quartz(monkeypatch):
    """Stand in for Quartz so the tap logic can be driven directly."""
    module = types.ModuleType("Quartz")
    module.kCGEventTapDisabledByTimeout = 0xFFFFFFFE
    module.kCGEventTapDisabledByUserInput = 0xFFFFFFFF
    module.kCGKeyboardEventKeycode = 9
    module.kCGEventFlagMaskSecondaryFn = 0x800000
    module.enabled_calls: list[bool] = []

    module._keycode = 0
    module._flags = 0
    module.CGEventGetIntegerValueField = lambda event, field: event["keycode"]
    module.CGEventGetFlags = lambda event: event["flags"]
    module.CGEventTapEnable = lambda tap, enable: module.enabled_calls.append(enable)

    monkeypatch.setitem(sys.modules, "Quartz", module)
    monkeypatch.setattr("skydict.macos.hotkey.require_listen_access", lambda: None)
    return module


def event(keycode: int, flags: int) -> dict:
    return {"keycode": keycode, "flags": flags}


@pytest.fixture
def listener():
    seen: list[HotkeyEvent] = []
    listener = ModifierHotkeyListener("right_option", on_event=seen.append)
    listener.seen = seen
    return listener


RIGHT_OPTION = MODIFIER_KEYCODES["right_option"]
LEFT_OPTION = MODIFIER_KEYCODES["left_option"]
R_MASK = DEVICE_FLAG_MASKS["right_option"]
L_MASK = DEVICE_FLAG_MASKS["left_option"]


def test_press_and_release_are_reported(fake_quartz, listener):
    listener._handle(None, 12, event(RIGHT_OPTION, R_MASK), None)
    listener._handle(None, 12, event(RIGHT_OPTION, 0), None)

    assert listener.seen == [HotkeyEvent.PRESSED, HotkeyEvent.RELEASED]
    assert not listener.is_held


def test_other_modifier_keys_are_ignored(fake_quartz, listener):
    listener._handle(None, 12, event(LEFT_OPTION, L_MASK), None)
    listener._handle(None, 12, event(LEFT_OPTION, 0), None)

    assert listener.seen == []


def test_release_is_detected_while_the_other_option_is_held(fake_quartz, listener):
    """The shared Alternate bit stays set here — only the device bit reveals the release."""
    listener._handle(None, 12, event(RIGHT_OPTION, R_MASK), None)
    # Left Option goes down too, then right is released: left's bit is still set.
    listener._handle(None, 12, event(LEFT_OPTION, R_MASK | L_MASK), None)
    listener._handle(None, 12, event(RIGHT_OPTION, L_MASK), None)

    assert listener.seen == [HotkeyEvent.PRESSED, HotkeyEvent.RELEASED]


def test_repeated_press_events_emit_once(fake_quartz, listener):
    for _ in range(3):
        listener._handle(None, 12, event(RIGHT_OPTION, R_MASK), None)

    assert listener.seen == [HotkeyEvent.PRESSED]


def test_release_without_press_is_ignored(fake_quartz, listener):
    listener._handle(None, 12, event(RIGHT_OPTION, 0), None)

    assert listener.seen == []


def test_disabled_tap_is_re_enabled(fake_quartz, listener):
    listener._tap = object()

    listener._handle(None, fake_quartz.kCGEventTapDisabledByTimeout, event(0, 0), None)

    assert fake_quartz.enabled_calls == [True]


def test_handler_exception_does_not_break_the_tap(fake_quartz):
    def explode(_event):
        raise RuntimeError("handler crashed")

    listener = ModifierHotkeyListener("right_option", on_event=explode)

    listener._handle(None, 12, event(RIGHT_OPTION, R_MASK), None)

    assert listener.is_held  # state still advanced despite the failure


def test_fn_uses_the_public_mask(fake_quartz):
    seen: list[HotkeyEvent] = []
    listener = ModifierHotkeyListener("fn", on_event=seen.append)

    listener._handle(
        None, 12, event(MODIFIER_KEYCODES["fn"], fake_quartz.kCGEventFlagMaskSecondaryFn), None
    )

    assert seen == [HotkeyEvent.PRESSED]


def test_unknown_trigger_is_rejected():
    with pytest.raises(ValueError, match="Unknown trigger"):
        ModifierHotkeyListener("caps_lock")


def test_every_trigger_has_a_mask():
    for name in MODIFIER_KEYCODES:
        if name != "fn":
            assert name in DEVICE_FLAG_MASKS, f"{name} has no device flag mask"
