from __future__ import annotations

import types

import numpy as np
import pytest

from skydict.config import Settings
from skydict.pipeline import DictationSession, State, TooShortError
from skydict.stt.base import SttError, TranscriptResult


class FakeBackend:
    name = "fake"
    model = "fake-model"

    def __init__(self, text: str = "распознанный текст", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.warmed_up = False
        self.calls: list[tuple[int, int]] = []

    def warmup(self) -> None:
        self.warmed_up = True

    def transcribe(self, audio, sample_rate, language=None) -> TranscriptResult:
        if self.error:
            raise self.error
        self.calls.append((len(audio), sample_rate))
        return TranscriptResult(
            text=self.text, backend=self.name, model=self.model, language=language
        )


class UpperProcessor:
    name = "upper"

    def process(self, result: TranscriptResult) -> str:
        return result.text.upper()


@pytest.fixture
def audio() -> np.ndarray:
    return np.zeros(16_000, dtype=np.float32)


def test_transcribe_audio_runs_the_full_chain(audio):
    delivered: list[str] = []
    session = DictationSession(
        Settings(), backend=FakeBackend("привет"), processor=UpperProcessor(),
        deliver=delivered.append,
    )

    result = session.transcribe_audio(audio)

    assert result.text == "ПРИВЕТ"
    assert result.transcript.text == "привет"
    assert result.audio_duration == pytest.approx(1.0)
    assert delivered == ["ПРИВЕТ"]
    assert session.state is State.IDLE


def test_state_transitions_are_reported_in_order(audio):
    session = DictationSession(Settings(), backend=FakeBackend())
    seen: list[State] = []
    session.add_listener(lambda state, _: seen.append(state))

    session.transcribe_audio(audio)

    assert seen == [State.TRANSCRIBING, State.DELIVERING, State.IDLE]


def test_short_audio_is_rejected_before_hitting_the_backend():
    backend = FakeBackend()
    session = DictationSession(Settings(), backend=backend)

    with pytest.raises(TooShortError):
        session.transcribe_audio(np.zeros(800, dtype=np.float32))  # 50 ms

    assert backend.calls == []
    assert session.state is State.IDLE


def test_backend_failure_moves_to_error_state(audio):
    session = DictationSession(Settings(), backend=FakeBackend(error=SttError("boom")))

    with pytest.raises(SttError, match="boom"):
        session.transcribe_audio(audio)

    assert session.state is State.ERROR
    assert isinstance(session.last_error, SttError)


def test_success_after_error_clears_last_error(audio):
    backend = FakeBackend(error=SttError("boom"))
    session = DictationSession(Settings(), backend=backend)
    with pytest.raises(SttError):
        session.transcribe_audio(audio)

    backend.error = None
    session.transcribe_audio(audio)

    assert session.state is State.IDLE
    assert session.last_error is None


def test_empty_text_is_not_delivered(audio):
    delivered: list[str] = []
    session = DictationSession(Settings(), backend=FakeBackend(""), deliver=delivered.append)

    result = session.transcribe_audio(audio)

    assert result.text == ""
    assert delivered == []


def test_broken_listener_does_not_break_dictation(audio):
    session = DictationSession(Settings(), backend=FakeBackend())
    session.add_listener(lambda state, _: (_ for _ in ()).throw(RuntimeError("ui crashed")))

    assert session.transcribe_audio(audio).text == "распознанный текст"


def test_stop_without_recording_is_an_error():
    session = DictationSession(Settings(), backend=FakeBackend())

    with pytest.raises(RuntimeError, match="while not recording"):
        session.stop_and_transcribe()


def test_warmup_reaches_the_backend():
    backend = FakeBackend()
    DictationSession(Settings(), backend=backend).warmup()

    assert backend.warmed_up


def test_warmup_skips_the_vad_when_it_is_not_used(monkeypatch):
    session = DictationSession(Settings(), backend=FakeBackend())
    monkeypatch.setattr(session, "_get_vad", lambda: pytest.fail("VAD loaded unnecessarily"))

    session.warmup()


def test_warmup_loads_the_vad_when_requested():
    session = DictationSession(Settings(), backend=FakeBackend())
    loaded: list[str] = []
    session._vad = types.SimpleNamespace(
        warmup=lambda: loaded.append("warmup"), reset=lambda: None
    )

    session.warmup(use_vad=True)

    assert loaded == ["warmup"]


def test_vad_is_loaded_before_the_stream_opens(fake_sd):
    """A mid-recording model download would swallow the start of the sentence."""
    session = DictationSession(Settings(), backend=FakeBackend())
    order: list[str] = []
    session._vad = types.SimpleNamespace(
        warmup=lambda: order.append("vad-warmup"),
        reset=lambda: None,
        push=lambda block: types.SimpleNamespace(state=None),
    )
    fake_sd.on_start = lambda: order.append("stream-start")

    session.start_recording(use_vad=True)
    session.cancel()

    assert order[:2] == ["vad-warmup", "stream-start"]
