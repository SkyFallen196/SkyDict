"""Microphone capture.

Records 16 kHz mono float32 — the native input format for both Whisper and GigaAM, so
audio never needs resampling on its way to a backend.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

import numpy as np

from ..config import SAMPLE_RATE, AudioSettings

log = logging.getLogger(__name__)

#: Frames per callback: 32 ms, a whole number of the 512-sample windows Silero VAD wants.
BLOCK_SIZE = 512


class AudioError(RuntimeError):
    """Microphone unavailable, permission denied, or the stream failed mid-recording."""


def list_devices() -> list[dict]:
    """Return the available input devices as dicts with index, name and channel count."""
    import sounddevice as sd

    devices = []
    default_input = sd.default.device[0] if sd.default.device else None
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] < 1:
            continue
        devices.append(
            {
                "index": index,
                "name": device["name"],
                "channels": device["max_input_channels"],
                "default_samplerate": device["default_samplerate"],
                "is_default": index == default_input,
            }
        )
    return devices


class Recorder:
    """Start/stop microphone capture, accumulating audio in memory.

    Blocks are pushed onto a queue by the PortAudio callback thread and drained by a
    reader thread, so a slow consumer never stalls the audio callback.
    """

    def __init__(
        self,
        settings: AudioSettings | None = None,
        sample_rate: int = SAMPLE_RATE,
        on_block: Callable[[np.ndarray], None] | None = None,
    ) -> None:
        self.settings = settings or AudioSettings()
        self.sample_rate = sample_rate
        #: Called with every captured block — used by the VAD to watch for silence.
        self.on_block = on_block

        self._stream = None
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue()
        self._blocks: list[np.ndarray] = []
        self._reader: threading.Thread | None = None
        self._error: Exception | None = None
        self._recording = False

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self) -> None:
        if self._recording:
            return

        import sounddevice as sd

        self._blocks = []
        self._error = None
        self._queue = queue.Queue()

        def callback(indata, frames, time_info, status) -> None:  # noqa: ARG001
            if status:
                log.debug("Audio callback status: %s", status)
            # indata is reused by PortAudio between calls, so it must be copied.
            self._queue.put(indata[:, 0].copy())

        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=BLOCK_SIZE,
                device=self.settings.input_device,
                callback=callback,
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            raise AudioError(f"Could not open the microphone: {exc}") from exc

        self._recording = True
        self._reader = threading.Thread(target=self._drain, name="skydict-audio", daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        while True:
            block = self._queue.get()
            if block is None:
                return
            self._blocks.append(block)
            if self.on_block is not None:
                try:
                    self.on_block(block)
                except Exception as exc:  # a broken listener must not kill the recording
                    log.warning("on_block listener failed: %s", exc)
                    self._error = exc

    def stop(self) -> np.ndarray:
        """Stop capture and return everything recorded as float32 mono."""
        if not self._recording:
            return np.zeros(0, dtype=np.float32)

        self._recording = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        self._queue.put(None)
        if self._reader is not None:
            self._reader.join(timeout=2.0)
            self._reader = None

        if not self._blocks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._blocks).astype(np.float32)

    def abort(self) -> None:
        """Stop capture and throw the audio away."""
        self.stop()
        self._blocks = []

    def __enter__(self) -> Recorder:
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        if self._recording:
            self.abort()
