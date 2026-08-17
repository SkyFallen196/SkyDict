"""Controller tests: hotkey events in, dictations out, with the tap and audio faked."""

from __future__ import annotations

import threading
import types

import numpy as np
import pytest

from skydict.config import Settings
from skydict.controller import DictationController
from skydict.macos.hotkey import HotkeyEvent
from skydict.pipeline import DictationSession
from skydict.stt.base import SttError, TranscriptResult


class FakeBackend:
    name = "fake"
    model = "fake-model"

    def __init__(self, text: str = "текст", error: Exception | None = None) -> None:
        self.text = text
        self.error = error

    def warmup(self) -> None:
        pass

    def transcribe(self, audio, sample_rate, language=None) -> TranscriptResult:
        if self.error:
            raise self.error
        return TranscriptResult(text=self.text, backend=self.name, model=self.model)


@pytest.fixture
def no_listener(monkeypatch):
    """Replace the event tap: the controller is driven by calling _on_hotkey directly."""
    monkeypatch.setattr("skydict.controller.ModifierHotkeyListener.start", lambda self: None)
    monkeypatch.setattr("skydict.controller.ModifierHotkeyListener.stop", lambda self: None)


@pytest.fixture
def clipboard_delivery(monkeypatch):
    """Force clipboard-only delivery so no Accessibility permission is involved."""
    monkeypatch.setattr("skydict.output.inserter.check_post_access", lambda: True)


def build(settings: Settings, backend: FakeBackend, delivered: list[str]):
    session = DictationSession(settings, backend=backend, deliver=delivered.append)
    results: list = []
    errors: list[Exception] = []
    controller = DictationController(
        settings,
        session=session,
        on_result=results.append,
        on_error=errors.append,
    )
    controller.results = results
    controller.errors = errors
    return controller


@pytest.fixture
def settings() -> Settings:
    s = Settings(insert_mode="clipboard_only")
    s.audio.min_recording_duration = 0.0
    return s


def wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = threading.Event()
    step = 0.01
    waited = 0.0
    while waited < timeout:
        if predicate():
            return True
        deadline.wait(step)
        waited += step
    return predicate()


def test_hold_records_between_press_and_release(fake_sd, no_listener, settings):
    delivered: list[str] = []
    controller = build(settings, FakeBackend("привет"), delivered)

    controller._on_hotkey(HotkeyEvent.PRESSED)
    assert controller.is_recording

    controller._on_hotkey(HotkeyEvent.RELEASED)

    assert wait_for(lambda: delivered == ["привет"])
    assert not controller.is_recording
    assert controller.results[0].text == "привет"


def test_toggle_needs_two_presses(fake_sd, no_listener, settings):
    settings.trigger_mode = "toggle"
    delivered: list[str] = []
    controller = build(settings, FakeBackend("текст"), delivered)

    controller._on_hotkey(HotkeyEvent.PRESSED)
    controller._on_hotkey(HotkeyEvent.RELEASED)  # ignored in toggle mode
    assert controller.is_recording
    assert delivered == []

    controller._on_hotkey(HotkeyEvent.PRESSED)

    assert wait_for(lambda: delivered == ["текст"])


def test_second_press_while_recording_is_ignored_in_hold_mode(fake_sd, no_listener, settings):
    controller = build(settings, FakeBackend(), [])

    controller._on_hotkey(HotkeyEvent.PRESSED)
    controller._on_hotkey(HotkeyEvent.PRESSED)

    assert controller.is_recording
    controller._on_hotkey(HotkeyEvent.RELEASED)
    assert wait_for(lambda: not controller.is_recording)


def test_release_without_press_does_nothing(fake_sd, no_listener, settings):
    delivered: list[str] = []
    controller = build(settings, FakeBackend(), delivered)

    controller._on_hotkey(HotkeyEvent.RELEASED)

    assert delivered == []
    assert controller.results == []


def test_too_short_recording_is_not_an_error(fake_sd, no_listener, settings):
    settings.audio.min_recording_duration = 10.0
    delivered: list[str] = []
    controller = build(settings, FakeBackend(), delivered)

    controller._on_hotkey(HotkeyEvent.PRESSED)
    controller._on_hotkey(HotkeyEvent.RELEASED)

    assert wait_for(lambda: not controller.is_recording)
    assert delivered == []
    assert controller.errors == []
    assert controller.results == []


def test_backend_failure_reaches_the_error_callback(fake_sd, no_listener, settings):
    controller = build(settings, FakeBackend(error=SttError("no network")), [])

    controller._on_hotkey(HotkeyEvent.PRESSED)
    controller._on_hotkey(HotkeyEvent.RELEASED)

    assert wait_for(lambda: len(controller.errors) == 1)
    assert isinstance(controller.errors[0], SttError)


def test_transcription_runs_off_the_hotkey_thread(fake_sd, no_listener, settings):
    """A tap that blocks gets disabled by macOS, so _finish must return immediately."""
    started = threading.Event()
    release_it = threading.Event()

    class SlowBackend(FakeBackend):
        def transcribe(self, audio, sample_rate, language=None):
            started.set()
            release_it.wait(timeout=2.0)
            return TranscriptResult(text="slow", backend="fake", model="fake")

    controller = build(settings, SlowBackend(), [])

    controller._on_hotkey(HotkeyEvent.PRESSED)
    controller._on_hotkey(HotkeyEvent.RELEASED)  # must not block here

    assert started.wait(timeout=2.0)
    release_it.set()
    assert wait_for(lambda: len(controller.results) == 1)


def test_recording_failure_is_reported_and_leaves_idle(fake_sd, no_listener, settings, monkeypatch):
    controller = build(settings, FakeBackend(), [])
    monkeypatch.setattr(
        controller.session, "start_recording", lambda **kw: (_ for _ in ()).throw(OSError("no mic"))
    )

    controller._on_hotkey(HotkeyEvent.PRESSED)

    assert not controller.is_recording
    assert isinstance(controller.errors[0], OSError)


def test_vad_stop_finishes_the_dictation(fake_sd, no_listener, settings, monkeypatch):
    settings.trigger_mode = "hold_vad"
    delivered: list[str] = []
    controller = build(settings, FakeBackend("по тишине"), delivered)

    # The VAD says "finished" as soon as the watcher asks.
    fake_vad = types.SimpleNamespace(
        warmup=lambda: None, reset=lambda: None, push=lambda block: None
    )
    monkeypatch.setattr(controller.session, "wait_for_vad_stop", lambda timeout=None: True)
    monkeypatch.setattr(controller.session, "_get_vad", lambda: fake_vad)

    controller._on_hotkey(HotkeyEvent.PRESSED)

    assert wait_for(lambda: delivered == ["по тишине"])
    assert not controller.is_recording


def test_stop_cancels_an_active_recording(fake_sd, no_listener, settings):
    delivered: list[str] = []
    controller = build(settings, FakeBackend(), delivered)

    controller._on_hotkey(HotkeyEvent.PRESSED)
    controller.stop()

    assert not controller.is_recording
    assert delivered == []


def test_delivery_reaches_the_inserter(fake_sd, no_listener, monkeypatch):
    """The controller's own session must be wired to the configured inserter."""
    monkeypatch.setattr("skydict.output.inserter.check_post_access", lambda: True)
    settings = Settings(insert_mode="clipboard_only")
    settings.audio.min_recording_duration = 0.0

    controller = DictationController(settings, session=None)
    controller.session.backend = FakeBackend("вставлено")

    pasted: list[str] = []
    monkeypatch.setattr(controller.inserter, "deliver", pasted.append)
    controller.session.deliver = controller.inserter.deliver

    controller.session.transcribe_audio(np.zeros(16_000, dtype=np.float32))

    assert pasted == ["вставлено"]
