"""Streaming voice activity detection for auto-stop recording.

onnx-asr ships a Silero VAD, but its API segments a finished waveform in one batch.
Auto-stop needs the opposite: a verdict per 512-sample window as audio arrives. So this
runs the same ``istupakov/silero-vad-onnx`` weights through onnxruntime directly,
keeping the recurrent state between windows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

import numpy as np

from ..config import SAMPLE_RATE, VadSettings

log = logging.getLogger(__name__)

VAD_REPO = "istupakov/silero-vad-onnx"
VAD_FILE = "silero_vad.onnx"

#: Silero's fixed geometry at 16 kHz: 512-sample hop with 64 samples of left context.
HOP_SIZE = 512
CONTEXT_SIZE = 64


class VadState(Enum):
    SILENCE = "silence"
    SPEECH = "speech"
    FINISHED = "finished"


@dataclass(slots=True)
class VadVerdict:
    state: VadState
    probability: float
    speech_duration: float
    silence_duration: float


class SileroStreamVad:
    """Feeds audio blocks through Silero VAD and reports when an utterance has ended.

    Speech is considered finished once ``silence_duration`` of quiet follows at least
    ``min_speech_duration`` of speech — so background noise before anyone talks never
    triggers a stop, and neither does a brief pause mid-sentence.
    """

    def __init__(self, settings: VadSettings | None = None, sample_rate: int = SAMPLE_RATE) -> None:
        if sample_rate != SAMPLE_RATE:
            raise ValueError(f"Silero VAD needs {SAMPLE_RATE} Hz audio, got {sample_rate}")
        self.settings = settings or VadSettings()
        self.sample_rate = sample_rate

        self._session = None
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT_SIZE), dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)

        self._speech_samples = 0
        self._silence_samples = 0
        self._total_samples = 0
        self._triggered = False
        self._finished = False

    def _load(self):
        if self._session is not None:
            return self._session
        import onnxruntime as rt
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(VAD_REPO, VAD_FILE)
        self._session = rt.InferenceSession(path, providers=["CPUExecutionProvider"])
        return self._session

    def warmup(self) -> None:
        self._load()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT_SIZE), dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)
        self._speech_samples = 0
        self._silence_samples = 0
        self._total_samples = 0
        self._triggered = False
        self._finished = False

    @property
    def is_finished(self) -> bool:
        return self._finished

    def _probability(self, window: np.ndarray) -> float:
        session = self._load()
        frame = np.concatenate([self._context, window.reshape(1, -1)], axis=1).astype(np.float32)
        output, new_state = session.run(
            ["output", "stateN"],
            {
                "input": frame,
                "state": self._state,
                "sr": np.array([self.sample_rate], dtype=np.int64),
            },
        )
        self._state = new_state
        self._context = window.reshape(1, -1)[:, -CONTEXT_SIZE:]
        return float(output[0, 0])

    def push(self, block: np.ndarray) -> VadVerdict:
        """Feed one block of audio and get the current verdict.

        Blocks of any length are accepted; leftover samples are buffered until a full
        512-sample window is available.
        """
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        self._total_samples += len(block)
        self._pending = np.concatenate([self._pending, block]) if self._pending.size else block

        probability = 0.0
        while len(self._pending) >= HOP_SIZE:
            window = self._pending[:HOP_SIZE]
            self._pending = self._pending[HOP_SIZE:]

            probability = self._probability(window)
            if probability >= self.settings.speech_threshold:
                self._speech_samples += HOP_SIZE
                self._silence_samples = 0
                if self._speech_samples / self.sample_rate >= self.settings.min_speech_duration:
                    self._triggered = True
            else:
                self._silence_samples += HOP_SIZE

        silence_duration = self._silence_samples / self.sample_rate
        if self._triggered and silence_duration >= self.settings.silence_duration:
            self._finished = True
        if self._total_samples / self.sample_rate >= self.settings.max_duration:
            log.info("VAD hit the %.0fs cap, stopping", self.settings.max_duration)
            self._finished = True

        if self._finished:
            state = VadState.FINISHED
        elif self._triggered and self._silence_samples == 0:
            state = VadState.SPEECH
        else:
            state = VadState.SILENCE

        return VadVerdict(
            state=state,
            probability=probability,
            speech_duration=self._speech_samples / self.sample_rate,
            silence_duration=silence_duration,
        )
