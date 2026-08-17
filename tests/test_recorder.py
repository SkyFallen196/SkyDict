"""Recorder tests against a fake sounddevice module — no real microphone involved."""

from __future__ import annotations

import numpy as np
import pytest

from skydict.audio.recorder import BLOCK_SIZE, AudioError, Recorder, list_devices
from skydict.config import AudioSettings


def test_records_and_returns_concatenated_audio(fake_sd):
    recorder = Recorder()
    recorder.start()
    audio = recorder.stop()

    assert audio.dtype == np.float32
    assert len(audio) == 5 * BLOCK_SIZE
    assert not recorder.is_recording


def test_stream_is_opened_as_16khz_mono_float32(fake_sd):
    recorder = Recorder()
    recorder.start()
    recorder.stop()

    kwargs = fake_sd.streams[0].kwargs
    assert kwargs["samplerate"] == 16_000
    assert kwargs["channels"] == 1
    assert kwargs["dtype"] == "float32"
    assert kwargs["blocksize"] == BLOCK_SIZE


def test_configured_input_device_is_passed_through(fake_sd):
    Recorder(AudioSettings(input_device=2)).start()

    assert fake_sd.streams[0].kwargs["device"] == 2


def test_stream_is_closed_after_stop(fake_sd):
    recorder = Recorder()
    recorder.start()
    recorder.stop()

    assert fake_sd.streams[0].closed


def test_on_block_listener_sees_every_block(fake_sd):
    seen: list[int] = []
    recorder = Recorder(on_block=lambda block: seen.append(len(block)))

    recorder.start()
    recorder.stop()

    assert seen == [BLOCK_SIZE] * 5


def test_broken_listener_does_not_lose_audio(fake_sd):
    def explode(block):
        raise RuntimeError("vad crashed")

    recorder = Recorder(on_block=explode)
    recorder.start()

    assert len(recorder.stop()) == 5 * BLOCK_SIZE


def test_abort_discards_the_audio(fake_sd):
    recorder = Recorder()
    recorder.start()
    recorder.abort()

    assert len(recorder.stop()) == 0


def test_stop_without_start_returns_empty(fake_sd):
    assert len(Recorder().stop()) == 0


def test_double_start_is_ignored(fake_sd):
    recorder = Recorder()
    recorder.start()
    recorder.start()

    assert len(fake_sd.streams) == 1
    recorder.stop()


def test_unavailable_microphone_raises_audio_error(fake_sd):
    fake_sd.fail_on_start = True

    with pytest.raises(AudioError, match="Could not open the microphone"):
        Recorder().start()


def test_context_manager_aborts_on_exit(fake_sd):
    with Recorder() as recorder:
        assert recorder.is_recording

    assert not recorder.is_recording
    assert fake_sd.streams[0].closed


def test_list_devices_skips_output_only_and_marks_default(fake_sd):
    devices = list_devices()

    assert [d["name"] for d in devices] == ["MacBook Air Microphone", "USB Mic"]
    assert devices[0]["is_default"]
    assert not devices[1]["is_default"]
