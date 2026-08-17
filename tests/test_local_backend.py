"""End-to-end checks against the real GigaAM v3 weights.

Marked slow: the first run downloads roughly 1.5 GB from Hugging Face.
Run with: pytest -m slow
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from skydict.config import SAMPLE_RATE, LocalBackendSettings, Settings
from skydict.pipeline import DictationSession
from skydict.stt.base import read_wav
from skydict.stt.local_onnx import LocalOnnxBackend

SAMPLE = Path(__file__).parent / "data" / "sample_ru.wav"

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def backend() -> LocalOnnxBackend:
    stt = LocalOnnxBackend(LocalBackendSettings())
    stt.warmup()
    return stt


def test_warmup_loads_the_model(backend):
    assert backend.is_loaded


def test_transcribes_russian_speech(backend):
    audio, sample_rate = read_wav(str(SAMPLE))

    result = backend.transcribe(audio, sample_rate)

    assert result.backend == "local"
    assert result.model == "gigaam-v3-e2e-rnnt"
    assert result.duration == pytest.approx(len(audio) / sample_rate)
    # The sample says "Привет! Это тестовая запись для проверки распознавания речи…"
    lowered = result.text.lower()
    assert "привет" in lowered
    assert "распознавания" in lowered


def test_e2e_model_returns_punctuated_text(backend):
    audio, sample_rate = read_wav(str(SAMPLE))

    text = backend.transcribe(audio, sample_rate).text

    assert text[0].isupper(), f"expected capitalised output, got {text!r}"
    assert any(mark in text for mark in ".,!?"), f"expected punctuation, got {text!r}"


def test_silence_produces_no_text(backend):
    result = backend.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), SAMPLE_RATE)

    assert result.text == ""


def test_pipeline_delivers_local_transcription():
    settings = Settings(backend="local")
    delivered: list[str] = []
    session = DictationSession(settings, deliver=delivered.append)

    audio, sample_rate = read_wav(str(SAMPLE))
    result = session.transcribe_audio(audio, sample_rate)

    assert delivered == [result.text]
    assert "привет" in result.text.lower()
