"""The dictation pipeline: record → recognise → post-process → deliver.

:class:`DictationSession` owns the state machine and reports every transition through a
listener callback. The CLI logs those transitions; the menubar app will drive its status
icon from the same events, so the UI layer needs no logic of its own.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

import numpy as np

from .audio.recorder import Recorder
from .audio.vad import SileroStreamVad, VadState
from .config import SAMPLE_RATE, Settings
from .post.base import PassthroughProcessor, PostProcessor
from .stt.base import SttBackend, SttError, TranscriptResult
from .stt.registry import build_backend

log = logging.getLogger(__name__)


class State(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    DELIVERING = "delivering"
    ERROR = "error"


@dataclass(slots=True)
class DictationResult:
    text: str
    transcript: TranscriptResult
    audio_duration: float


StateListener = Callable[[State, "DictationSession"], None]


class TooShortError(RuntimeError):
    """The recording was shorter than ``audio.min_recording_duration`` — an accidental tap."""


class DictationSession:
    """One microphone-to-text pipeline, reusable across many dictations.

    The backend is created once and kept warm, so the model load and HTTP connection
    setup are paid a single time rather than on every hotkey press.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        backend: SttBackend | None = None,
        processor: PostProcessor | None = None,
        deliver: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.backend = backend or build_backend(self.settings)
        self.processor = processor or PassthroughProcessor()
        #: Where finished text goes. The CLI prints it; the platform layer pastes it.
        self.deliver = deliver

        self._state = State.IDLE
        self._listeners: list[StateListener] = []
        self._lock = threading.Lock()
        self._recorder: Recorder | None = None
        self._vad: SileroStreamVad | None = None
        self._stop_requested = threading.Event()
        self.last_error: Exception | None = None

    @property
    def state(self) -> State:
        return self._state

    def add_listener(self, listener: StateListener) -> None:
        self._listeners.append(listener)

    def _set_state(self, state: State) -> None:
        self._state = state
        for listener in self._listeners:
            try:
                listener(state, self)
            except Exception as exc:  # a broken listener must not break dictation
                log.warning("State listener failed: %s", exc)

    def warmup(self, use_vad: bool | None = None) -> None:
        """Load the models and open connections ahead of the first dictation."""
        self.backend.warmup()
        if use_vad is None:
            use_vad = self.settings.trigger_mode == "hold_vad"
        if use_vad:
            self._get_vad().warmup()

    def _get_vad(self) -> SileroStreamVad:
        if self._vad is None:
            self._vad = SileroStreamVad(self.settings.vad)
        return self._vad

    def start_recording(self, use_vad: bool | None = None) -> None:
        """Begin capturing. With VAD enabled, recording stops itself after silence."""
        with self._lock:
            if self._state is not State.IDLE and self._state is not State.ERROR:
                log.debug("start_recording ignored in state %s", self._state)
                return

            use_vad = self.settings.trigger_mode == "hold_vad" if use_vad is None else use_vad
            self._stop_requested.clear()

            on_block = None
            if use_vad:
                vad = self._get_vad()
                # Load before the stream opens: downloading mid-recording would stall the
                # first blocks and swallow the start of the sentence.
                vad.warmup()
                vad.reset()

                def on_block(block: np.ndarray) -> None:
                    if vad.push(block).state is VadState.FINISHED:
                        self._stop_requested.set()

            self._recorder = Recorder(self.settings.audio, SAMPLE_RATE, on_block=on_block)
            self._recorder.start()
            self._set_state(State.RECORDING)

    @property
    def stop_requested(self) -> bool:
        """True once the VAD has decided the utterance is over."""
        return self._stop_requested.is_set()

    def wait_for_vad_stop(self, timeout: float | None = None) -> bool:
        """Block until the VAD calls the utterance finished. Returns False on timeout."""
        return self._stop_requested.wait(timeout)

    def cancel(self) -> None:
        """Abandon the current recording without transcribing it."""
        with self._lock:
            if self._recorder is not None:
                self._recorder.abort()
                self._recorder = None
            self._stop_requested.clear()
            self._set_state(State.IDLE)

    def stop_and_transcribe(self) -> DictationResult:
        """Stop recording, recognise the audio, post-process it and deliver the text."""
        with self._lock:
            if self._recorder is None:
                raise RuntimeError("stop_and_transcribe called while not recording")
            audio = self._recorder.stop()
            self._recorder = None
            self._stop_requested.clear()

        return self._run(audio)

    def transcribe_audio(
        self, audio: np.ndarray, sample_rate: int = SAMPLE_RATE
    ) -> DictationResult:
        """Run recognition on audio captured elsewhere, e.g. a WAV file from the CLI."""
        return self._run(audio, sample_rate)

    def _run(self, audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> DictationResult:
        try:
            return self._process(audio, sample_rate)
        except TooShortError:
            # An accidental hotkey tap is a normal outcome, not a failure to report.
            raise
        except Exception as exc:
            self.last_error = exc
            self._set_state(State.ERROR)
            raise

    def _process(self, audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> DictationResult:
        duration = len(audio) / sample_rate
        if duration < self.settings.audio.min_recording_duration:
            self._set_state(State.IDLE)
            raise TooShortError(
                f"Recording was {duration:.2f}s, shorter than the "
                f"{self.settings.audio.min_recording_duration:.2f}s minimum"
            )

        self._set_state(State.TRANSCRIBING)
        transcript = self.backend.transcribe(audio, sample_rate)

        self._set_state(State.DELIVERING)
        text = self.processor.process(transcript)
        if self.deliver is not None and text:
            self.deliver(text)

        self.last_error = None
        self._set_state(State.IDLE)
        return DictationResult(text=text, transcript=transcript, audio_duration=duration)

    def record_once(self, seconds: float | None = None, use_vad: bool = False) -> DictationResult:
        """Record and transcribe in one blocking call — the CLI's entry point.

        With ``use_vad`` the recording ends on silence; ``seconds`` caps it either way.
        """
        self.start_recording(use_vad=use_vad)
        try:
            if use_vad:
                self._stop_requested.wait(timeout=seconds or self.settings.vad.max_duration)
            elif seconds:
                self._stop_requested.wait(timeout=seconds)
            else:
                self._stop_requested.wait()
        except KeyboardInterrupt:
            self.cancel()
            raise
        return self.stop_and_transcribe()

    def request_stop(self) -> None:
        """Ask a blocking :meth:`record_once` to finish now."""
        self._stop_requested.set()


__all__ = ["DictationResult", "DictationSession", "State", "SttError", "TooShortError"]
