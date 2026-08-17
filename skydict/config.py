"""Application settings, persisted as JSON in the user's Application Support directory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from platformdirs import user_data_path
from pydantic import BaseModel, Field

from . import APP_NAME

BackendName = Literal["cloud", "local"]
TriggerMode = Literal["hold", "toggle", "hold_vad"]
InsertMode = Literal["paste", "clipboard_only"]

#: Sample rate used end to end. Both Whisper and GigaAM take 16 kHz mono natively,
#: so recording at this rate means no resampling anywhere in the pipeline.
SAMPLE_RATE = 16_000


def config_dir() -> Path:
    return user_data_path(APP_NAME, appauthor=False)


def config_path() -> Path:
    return config_dir() / "config.json"


class CloudBackendSettings(BaseModel):
    """Any OpenAI-compatible ``/v1/audio/transcriptions`` endpoint."""

    base_url: str = "https://api.groq.com/openai/v1"
    model: str = "whisper-large-v3-turbo"
    #: Keyring account name; the secret itself never lands in this file.
    credential_name: str = "groq"
    language: str | None = "ru"
    prompt: str | None = None
    timeout_seconds: float = 60.0
    max_retries: int = 3


class LocalBackendSettings(BaseModel):
    """A model served through onnx-asr (GigaAM, Whisper, Parakeet, Vosk, T-one).

    ``model`` is an onnx-asr model name (``gigaam-v3-e2e-rnnt``) or a full Hugging Face
    repo id (``onnx-community/whisper-large-v3-turbo``); either is downloaded on demand.
    """

    #: ``e2e`` variants emit punctuated and normalised text directly.
    model: str = "gigaam-v3-e2e-rnnt"
    #: Optional directory holding already-downloaded ONNX files, bypassing the HF fetch.
    model_path: str | None = None
    #: int8 keeps GigaAM's accuracy on dictation-length audio while cutting the encoder
    #: download from ~1 GB to ~250 MB and running faster on CPU. Set to None for fp32.
    quantization: str | None = "int8"
    #: Only Whisper and Canary accept a language hint; GigaAM ignores it.
    language: str | None = None
    #: onnxruntime execution providers, best first. CoreML uses the Apple Neural Engine
    #: and silently falls back to CPU on anything it cannot run.
    providers: list[str] = Field(default_factory=lambda: ["CPUExecutionProvider"])


class VadSettings(BaseModel):
    #: Silence needed before an auto-stop recording is considered finished.
    silence_duration: float = Field(default=1.5, gt=0)
    #: Speech shorter than this is treated as noise and never triggers a stop.
    min_speech_duration: float = Field(default=0.3, gt=0)
    speech_threshold: float = Field(default=0.5, ge=0, le=1)
    #: Hard cap so a stuck VAD can never record forever.
    max_duration: float = Field(default=300.0, gt=0)


class AudioSettings(BaseModel):
    #: sounddevice device index or name; None means the system default input.
    input_device: int | str | None = None
    #: Recordings shorter than this are discarded as accidental hotkey taps.
    min_recording_duration: float = Field(default=0.25, gt=0)


class Settings(BaseModel):
    backend: BackendName = "cloud"
    trigger_mode: TriggerMode = "hold"
    insert_mode: InsertMode = "paste"
    audio: AudioSettings = Field(default_factory=AudioSettings)
    cloud: CloudBackendSettings = Field(default_factory=CloudBackendSettings)
    local: LocalBackendSettings = Field(default_factory=LocalBackendSettings)
    vad: VadSettings = Field(default_factory=VadSettings)

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        """Read settings from disk, falling back to defaults when the file is absent."""
        path = path or config_path()
        if not path.exists():
            return cls()
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def save(self, path: Path | None = None) -> Path:
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path
