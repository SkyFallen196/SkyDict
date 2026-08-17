from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from skydict.audio.recorder import BLOCK_SIZE
from skydict.config import CloudBackendSettings, Settings
from skydict.secrets import env_var_name


class FakeStream:
    """Delivers a fixed number of blocks to the callback as soon as it is started."""

    def __init__(
        self,
        blocks: int = 5,
        fail_on_start: bool = False,
        on_start=None,
        **kwargs,
    ) -> None:
        self.kwargs = kwargs
        self.blocks = blocks
        self.fail_on_start = fail_on_start
        self.on_start = on_start
        self.started = False
        self.closed = False

    def start(self) -> None:
        if self.fail_on_start:
            raise OSError("Device unavailable")
        if self.on_start is not None:
            self.on_start()
        self.started = True
        callback = self.kwargs["callback"]
        for index in range(self.blocks):
            block = np.full((BLOCK_SIZE, 1), float(index) / 100, dtype=np.float32)
            callback(block, BLOCK_SIZE, None, None)

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_sd(monkeypatch):
    """Replace the sounddevice module so tests never touch real audio hardware."""
    module = types.ModuleType("sounddevice")
    module.streams: list[FakeStream] = []
    module.fail_on_start = False
    module.on_start = None

    def InputStream(**kwargs):  # noqa: N802 - mirrors the sounddevice API
        stream = FakeStream(
            fail_on_start=module.fail_on_start, on_start=module.on_start, **kwargs
        )
        module.streams.append(stream)
        return stream

    module.InputStream = InputStream
    module.default = types.SimpleNamespace(device=(1, 2))
    module.query_devices = lambda: [
        {"name": "Speakers", "max_input_channels": 0, "default_samplerate": 48_000},
        {"name": "MacBook Air Microphone", "max_input_channels": 1, "default_samplerate": 48_000},
        {"name": "USB Mic", "max_input_channels": 2, "default_samplerate": 44_100},
    ]

    monkeypatch.setitem(sys.modules, "sounddevice", module)
    return module


@pytest.fixture
def api_key(monkeypatch) -> str:
    """Provide a credential via the env fallback so tests never touch the real Keychain."""
    key = "test-key-123"
    monkeypatch.setenv(env_var_name("groq"), key)
    return key


@pytest.fixture
def cloud_settings() -> CloudBackendSettings:
    return CloudBackendSettings(
        base_url="https://api.example.test/v1",
        model="whisper-large-v3-turbo",
        credential_name="groq",
        language="ru",
        max_retries=3,
    )


@pytest.fixture
def settings(cloud_settings) -> Settings:
    return Settings(backend="cloud", cloud=cloud_settings)


@pytest.fixture
def tone() -> np.ndarray:
    """One second of a 440 Hz tone at 16 kHz."""
    t = np.linspace(0, 1.0, 16_000, endpoint=False, dtype=np.float32)
    return (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
