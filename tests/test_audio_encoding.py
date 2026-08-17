from __future__ import annotations

import io
import wave

import numpy as np
import pytest

from skydict.stt.base import SttError, read_wav, to_wav_bytes


def test_to_wav_bytes_roundtrips(tone, tmp_path):
    path = tmp_path / "tone.wav"
    path.write_bytes(to_wav_bytes(tone, 16_000))

    audio, sample_rate = read_wav(str(path))

    assert sample_rate == 16_000
    assert len(audio) == len(tone)
    # 16-bit quantisation costs at most 1/32768 per sample.
    np.testing.assert_allclose(audio, tone, atol=1e-4)


def test_to_wav_bytes_writes_mono_16bit(tone):
    with wave.open(io.BytesIO(to_wav_bytes(tone, 16_000)), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16_000
        assert wav.getnframes() == len(tone)


def test_to_wav_bytes_clips_out_of_range_samples():
    loud = np.array([-4.0, -1.0, 0.0, 1.0, 4.0], dtype=np.float32)

    with wave.open(io.BytesIO(to_wav_bytes(loud, 16_000)), "rb") as wav:
        pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)

    assert pcm.min() >= -32767
    assert pcm.max() <= 32767
    assert pcm[0] == pcm[1]  # both clipped to the negative rail
    assert pcm[3] == pcm[4]  # both clipped to the positive rail


def test_read_wav_averages_stereo_to_mono(tmp_path):
    left = np.full(100, 0.5, dtype=np.float32)
    right = np.full(100, -0.1, dtype=np.float32)
    interleaved = np.empty(200, dtype=np.float32)
    interleaved[0::2] = left
    interleaved[1::2] = right

    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes((interleaved * 32767).astype(np.int16).tobytes())

    audio, _ = read_wav(str(path))

    assert len(audio) == 100
    np.testing.assert_allclose(audio, np.full(100, 0.2), atol=1e-3)


def test_read_wav_rejects_non_16bit(tmp_path):
    path = tmp_path / "8bit.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(1)
        wav.setframerate(16_000)
        wav.writeframes(b"\x80" * 100)

    with pytest.raises(SttError, match="16-bit"):
        read_wav(str(path))
