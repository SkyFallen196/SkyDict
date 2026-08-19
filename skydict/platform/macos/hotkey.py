"""Global hotkey handling through a CGEventTap.

A tap is used rather than ``NSEvent.addGlobalMonitorForEventsMatchingMask_`` because the
monitor never reports key-up, and hold-to-talk is defined by the release. A tap also sees
events before the focused app does.

The default trigger is a modifier key held down — right Option. Modifiers are ideal for
push-to-talk: holding one alone does nothing in any app, so nothing has to be swallowed,
and the tap can stay listen-only. That keeps SkyDict from ever eating a keystroke another
app needed, which a suppressing tap risks whenever it misbehaves.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from ..base import HotkeyError, HotkeyEvent
from .permissions import require_listen_access

log = logging.getLogger(__name__)

#: Virtual keycodes for the modifier keys usable as a trigger.
MODIFIER_KEYCODES: dict[str, int] = {
    "right_option": 61,
    "left_option": 58,
    "right_command": 54,
    "left_command": 55,
    "right_control": 62,
    "left_control": 59,
    "right_shift": 60,
    "left_shift": 56,
    "fn": 63,
}

DEFAULT_TRIGGER = "right_option"

#: Device-dependent modifier bits from IOKit's IOLLEvent.h. The public
#: ``kCGEventFlagMask*`` constants only say "some Option is down", which breaks when both
#: Options are held and one is released — the shared bit stays set and the release is
#: missed, leaving the recording stuck on. These bits identify the physical key.
DEVICE_FLAG_MASKS: dict[str, int] = {
    "left_control": 0x00000001,
    "left_shift": 0x00000002,
    "right_shift": 0x00000004,
    "left_command": 0x00000008,
    "right_command": 0x00000010,
    "left_option": 0x00000020,
    "right_option": 0x00000040,
    "right_control": 0x00002000,
}


class ModifierHotkeyListener:
    """Watches one modifier key and reports press and release.

    Runs its own CFRunLoop on a dedicated thread, so it composes with an app that already
    owns the main runloop (the future menubar UI) as well as with a plain CLI process.
    """

    def __init__(
        self,
        trigger: str = DEFAULT_TRIGGER,
        on_event: Callable[[HotkeyEvent], None] | None = None,
    ) -> None:
        if trigger not in MODIFIER_KEYCODES:
            raise ValueError(
                f"Unknown trigger {trigger!r}. Choose one of: {', '.join(MODIFIER_KEYCODES)}"
            )
        self.trigger = trigger
        self.keycode = MODIFIER_KEYCODES[trigger]
        self.on_event = on_event

        self._tap = None
        self._runloop = None
        self._thread: threading.Thread | None = None
        self._held = False
        self._ready = threading.Event()
        self._error: Exception | None = None

    @property
    def is_held(self) -> bool:
        return self._held

    def _handle(self, proxy, event_type, event, refcon):  # noqa: ARG002 - CGEventTap ABI
        import Quartz

        # macOS disables a tap that is too slow or that trips on a system event; both are
        # recoverable by re-enabling it, otherwise the hotkey silently stops working.
        if event_type in (
            Quartz.kCGEventTapDisabledByTimeout,
            Quartz.kCGEventTapDisabledByUserInput,
        ):
            log.warning("Event tap was disabled by the system; re-enabling")
            Quartz.CGEventTapEnable(self._tap, True)
            return event

        keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        if keycode != self.keycode:
            return event

        # flagsChanged carries no up/down flag: the key is down when its modifier bit is
        # now set, and up when it is not.
        flags = Quartz.CGEventGetFlags(event)
        is_down = bool(flags & self._flag_mask(Quartz))

        if is_down and not self._held:
            self._held = True
            self._emit(HotkeyEvent.PRESSED)
        elif not is_down and self._held:
            self._held = False
            self._emit(HotkeyEvent.RELEASED)

        return event

    def _flag_mask(self, quartz) -> int:
        # Fn has no left/right variant, so the public mask is exact for it.
        if self.trigger == "fn":
            return quartz.kCGEventFlagMaskSecondaryFn
        return DEVICE_FLAG_MASKS[self.trigger]

    def _emit(self, event: HotkeyEvent) -> None:
        log.debug("Hotkey %s: %s", self.trigger, event.value)
        if self.on_event is None:
            return
        try:
            self.on_event(event)
        except Exception as exc:  # a handler crash must not kill the tap
            log.exception("Hotkey handler failed: %s", exc)

    def start(self) -> None:
        """Create the tap and run its loop on a background thread."""
        require_listen_access()

        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, name="skydict-hotkey", daemon=True)
        self._thread.start()

        if not self._ready.wait(timeout=5.0):
            raise HotkeyError("Timed out while starting the hotkey listener")
        if self._error is not None:
            raise self._error

    def _run(self) -> None:
        import Quartz

        try:
            self._tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                # Listen-only: events are observed and passed through untouched.
                Quartz.kCGEventTapOptionListenOnly,
                Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged),
                self._handle,
                None,
            )
            if self._tap is None:
                raise HotkeyError(
                    "Could not create the event tap. This usually means Accessibility "
                    "permission was revoked after startup."
                )

            source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
            self._runloop = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(self._runloop, source, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(self._tap, True)

            log.info("Listening for the %s key", self.trigger)
        except Exception as exc:
            self._error = exc
            self._ready.set()
            return

        self._ready.set()
        Quartz.CFRunLoopRun()

    def stop(self) -> None:
        import Quartz

        if self._tap is not None:
            Quartz.CGEventTapEnable(self._tap, False)
        if self._runloop is not None:
            Quartz.CFRunLoopStop(self._runloop)
            self._runloop = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._tap = None
        self._held = False

    def __enter__(self) -> ModifierHotkeyListener:
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()
