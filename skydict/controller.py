"""Wires the global hotkey to the dictation pipeline.

This is the piece the menubar UI will drive too: it owns the hotkey listener, decides
what a press and a release mean for the configured trigger mode, and keeps transcription
off the hotkey thread so the tap never stalls (macOS disables a tap that blocks).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from .config import Settings
from .macos.hotkey import DEFAULT_TRIGGER, HotkeyEvent, ModifierHotkeyListener
from .output.inserter import build_inserter
from .pipeline import DictationResult, DictationSession, TooShortError
from .stt.base import SttError
from .stt.registry import build_backend

log = logging.getLogger(__name__)


class DictationController:
    """Turns hotkey presses into dictations.

    Trigger modes:

    * ``hold`` — record while the key is held, transcribe on release.
    * ``toggle`` — press once to start, press again to stop.
    * ``hold_vad`` — press to start; recording ends on release or on silence, whichever
      comes first.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        session: DictationSession | None = None,
        trigger: str = DEFAULT_TRIGGER,
        on_result: Callable[[DictationResult], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.inserter = build_inserter(self.settings.insert_mode)
        self.session = session or DictationSession(
            self.settings, deliver=self.inserter.deliver
        )
        self.listener = ModifierHotkeyListener(trigger, on_event=self._on_hotkey)
        self.on_result = on_result
        self.on_error = on_error

        self._worker: threading.Thread | None = None
        self._vad_watcher: threading.Thread | None = None
        self._active = False
        self._lock = threading.Lock()

    @property
    def mode(self) -> str:
        return self.settings.trigger_mode

    @property
    def is_recording(self) -> bool:
        return self._active

    def start(self) -> None:
        self.session.warmup(use_vad=self.mode == "hold_vad")
        self.listener.start()

    def rebuild_backend(self) -> None:
        """Swap in the backend the settings now name, keeping the session's listeners.

        Used when the menubar changes backend at runtime. Warming up here means a
        missing key or an unavailable model is reported on the click that caused it,
        not on the next dictation.
        """
        self.session.backend = build_backend(self.settings)
        self.inserter = build_inserter(self.settings.insert_mode)
        self.session.deliver = self.inserter.deliver
        self.session.warmup(use_vad=self.mode == "hold_vad")

    def stop(self) -> None:
        self.listener.stop()
        if self._active:
            self.session.cancel()
            self._active = False

    def _on_hotkey(self, event: HotkeyEvent) -> None:
        if self.mode == "toggle":
            if event is HotkeyEvent.PRESSED:
                self._toggle()
            return

        if event is HotkeyEvent.PRESSED:
            self._begin()
        else:
            self._finish()

    def _toggle(self) -> None:
        if self._active:
            self._finish()
        else:
            self._begin()

    def _begin(self) -> None:
        with self._lock:
            if self._active:
                return
            use_vad = self.mode == "hold_vad"
            try:
                self.session.start_recording(use_vad=use_vad)
            except Exception as exc:
                self._report_error(exc)
                return
            self._active = True

        if use_vad:
            self._watch_for_silence()

    def _watch_for_silence(self) -> None:
        """End the dictation early if the VAD decides the user has stopped talking."""

        def wait() -> None:
            if self.session.wait_for_vad_stop(timeout=self.settings.vad.max_duration):
                self._finish()

        self._vad_watcher = threading.Thread(target=wait, name="skydict-vad", daemon=True)
        self._vad_watcher.start()

    def _finish(self) -> None:
        with self._lock:
            if not self._active:
                return
            self._active = False

        # Transcription runs off the hotkey thread: a tap that blocks gets disabled.
        self._worker = threading.Thread(
            target=self._transcribe, name="skydict-transcribe", daemon=True
        )
        self._worker.start()

    def _transcribe(self) -> None:
        try:
            result = self.session.stop_and_transcribe()
        except TooShortError as exc:
            log.info("%s", exc)
            return
        except (SttError, RuntimeError) as exc:
            self._report_error(exc)
            return

        log.info("Dictated %d characters", len(result.text))
        if self.on_result is not None:
            self.on_result(result)

    def _report_error(self, exc: Exception) -> None:
        log.error("Dictation failed: %s", exc)
        if self.on_error is not None:
            self.on_error(exc)

    def __enter__(self) -> DictationController:
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()
