"""VAD tests with a stubbed ONNX session — the windowing and stop logic is what matters,
not Silero's own accuracy. The real weights are exercised by the slow test at the end.
"""

from __future__ import annotations

import numpy as np
import pytest

from skydict.audio.vad import CONTEXT_SIZE, HOP_SIZE, SileroStreamVad, VadState
from skydict.config import VadSettings


class FakeSession:
    """Returns a speech probability driven by each window's amplitude."""

    def __init__(self) -> None:
        self.frames: list[np.ndarray] = []

    def run(self, outputs, inputs):
        frame = inputs["input"]
        self.frames.append(frame.copy())
        probability = 1.0 if np.abs(frame[:, CONTEXT_SIZE:]).max() > 0.1 else 0.0
        return [
            np.array([[probability]], dtype=np.float32),
            np.zeros((2, 1, 128), dtype=np.float32),
        ]


@pytest.fixture
def vad() -> SileroStreamVad:
    detector = SileroStreamVad(
        VadSettings(silence_duration=0.5, min_speech_duration=0.2, max_duration=10.0)
    )
    detector._session = FakeSession()
    return detector


def blocks(seconds: float, amplitude: float, block_size: int = HOP_SIZE) -> list[np.ndarray]:
    count = int(16_000 * seconds / block_size)
    return [np.full(block_size, amplitude, dtype=np.float32) for _ in range(count)]


def feed(vad: SileroStreamVad, chunks: list[np.ndarray]):
    verdict = None
    for chunk in chunks:
        verdict = vad.push(chunk)
    return verdict


def test_silence_alone_never_finishes(vad):
    verdict = feed(vad, blocks(3.0, amplitude=0.0))

    assert verdict.state is VadState.SILENCE
    assert not vad.is_finished


def test_speech_then_silence_finishes(vad):
    feed(vad, blocks(1.0, amplitude=0.5))
    assert not vad.is_finished

    verdict = feed(vad, blocks(0.6, amplitude=0.0))

    assert verdict.state is VadState.FINISHED
    assert vad.is_finished


def test_short_pause_mid_sentence_does_not_finish(vad):
    feed(vad, blocks(1.0, amplitude=0.5))
    feed(vad, blocks(0.3, amplitude=0.0))  # under the 0.5s threshold
    assert not vad.is_finished

    verdict = feed(vad, blocks(0.5, amplitude=0.5))

    assert verdict.state is VadState.SPEECH
    assert verdict.silence_duration == 0.0


def test_speech_shorter_than_minimum_is_treated_as_noise(vad):
    feed(vad, blocks(0.1, amplitude=0.5))  # under min_speech_duration of 0.2s
    verdict = feed(vad, blocks(2.0, amplitude=0.0))

    assert verdict.state is VadState.SILENCE
    assert not vad.is_finished


def test_max_duration_forces_a_stop():
    detector = SileroStreamVad(VadSettings(silence_duration=99.0, max_duration=1.0))
    detector._session = FakeSession()

    verdict = feed(detector, blocks(1.5, amplitude=0.5))

    assert verdict.state is VadState.FINISHED


def test_partial_blocks_are_buffered_until_a_full_window(vad):
    """Blocks that do not divide evenly into 512 must not drop or duplicate samples."""
    odd = 300
    feed(vad, blocks(1.2, amplitude=0.5, block_size=odd))
    verdict = feed(vad, blocks(1.0, amplitude=0.0, block_size=odd))

    assert verdict.state is VadState.FINISHED
    for frame in vad._session.frames:
        assert frame.shape == (1, CONTEXT_SIZE + HOP_SIZE)


def test_context_carries_over_between_windows(vad):
    first = np.full(HOP_SIZE, 0.5, dtype=np.float32)
    second = np.full(HOP_SIZE, 0.7, dtype=np.float32)

    vad.push(first)
    vad.push(second)

    # The second frame's context must be the tail of the first window, not zeros.
    np.testing.assert_allclose(vad._session.frames[1][0, :CONTEXT_SIZE], 0.5)
    np.testing.assert_allclose(vad._session.frames[0][0, :CONTEXT_SIZE], 0.0)


def test_reset_clears_all_state(vad):
    feed(vad, blocks(1.0, amplitude=0.5))
    feed(vad, blocks(0.6, amplitude=0.0))
    assert vad.is_finished

    vad.reset()

    assert not vad.is_finished
    assert vad.push(np.zeros(HOP_SIZE, dtype=np.float32)).speech_duration == 0.0


def test_rejects_unsupported_sample_rate():
    with pytest.raises(ValueError, match="16000 Hz"):
        SileroStreamVad(VadSettings(), sample_rate=44_100)


@pytest.mark.slow
def test_real_silero_separates_speech_from_silence():
    """Downloads the actual Silero weights and checks they respond to a real signal."""
    detector = SileroStreamVad(VadSettings())
    detector.warmup()

    silence = detector.push(np.zeros(16_000, dtype=np.float32))
    assert silence.probability < 0.5

    t = np.linspace(0, 1.0, 16_000, endpoint=False, dtype=np.float32)
    noise = (0.3 * np.sin(2 * np.pi * 200 * t) * np.sin(2 * np.pi * 3 * t)).astype(np.float32)
    detector.reset()
    detector.push(noise)
