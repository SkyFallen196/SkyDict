"""The interface every speech-to-text backend implements.

Audio moves through the app as float32 mono in [-1, 1] at :data:`~skydict.config.SAMPLE_RATE`.
Cloud backends wrap it into a WAV container in memory; local ones consume it directly.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np


class SttError(RuntimeError):
    """Any failure while transcribing — network, auth, or model level."""


@dataclass(slots=True)
class TranscriptResult:
    text: str
    backend: str
    model: str
    language: str | None = None
    duration: float | None = None
    raw: dict = field(default_factory=dict)


@runtime_checkable
class SttBackend(Protocol):
    name: str
    model: str

    def warmup(self) -> None:
        """Do any expensive setup ahead of the first real transcription."""

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int,
        language: str | None = None,
    ) -> TranscriptResult: ...


def to_wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    """Encode float32 mono audio as a 16-bit PCM WAV in memory."""
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return buffer.getvalue()


def read_wav(path: str) -> tuple[np.ndarray, int]:
    """Read a mono/stereo 16-bit PCM WAV into float32 mono. Stereo is averaged down."""
    with wave.open(path, "rb") as wav:
        if wav.getsampwidth() != 2:
            raise SttError(f"{path}: only 16-bit PCM WAV is supported")
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())

    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, sample_rate
