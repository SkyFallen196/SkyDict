"""Local transcription through onnx-asr.

onnx-asr needs only numpy and onnxruntime — no torch, transformers or ffmpeg — and it
accepts float32 arrays directly, so nothing is written to disk between recording and
recognition. The same backend serves GigaAM, Whisper, Parakeet, Vosk and T-one models;
GigaAM's ``e2e`` variants return punctuated, normalised text on their own.
"""

from __future__ import annotations

import logging
import threading

import numpy as np

from ..config import SAMPLE_RATE, LocalBackendSettings
from .base import SttError, TranscriptResult

log = logging.getLogger(__name__)


class LocalOnnxBackend:
    name = "local"

    def __init__(self, settings: LocalBackendSettings) -> None:
        self.settings = settings
        self.model = settings.model
        self._model = None
        # Loading pulls ~0.5-1 GB from Hugging Face on first use; the lock keeps a
        # menubar click and a hotkey press from starting two downloads at once.
        self._lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def _load(self):
        if self._model is not None:
            return self._model

        with self._lock:
            if self._model is not None:
                return self._model
            try:
                import onnx_asr
            except ImportError as exc:  # pragma: no cover - depends on install extras
                raise SttError(
                    "onnx-asr is not installed. Install it with: pip install 'onnx-asr[cpu,hub]'"
                ) from exc

            log.info("Loading local model '%s' (first run downloads it)", self.settings.model)
            kwargs: dict[str, object] = {}
            if self.settings.model_path:
                kwargs["path"] = self.settings.model_path
            if self.settings.quantization:
                kwargs["quantization"] = self.settings.quantization
            if self.settings.providers:
                kwargs["providers"] = self.settings.providers

            try:
                self._model = onnx_asr.load_model(self.settings.model, **kwargs)
            except Exception as exc:
                raise SttError(
                    f"Could not load local model '{self.settings.model}': {exc}"
                ) from exc
            log.info("Local model '%s' ready", self.settings.model)
        return self._model

    def warmup(self) -> None:
        """Load the model and run one throwaway pass so the first real call is fast."""
        model = self._load()
        try:
            model.recognize(np.zeros(SAMPLE_RATE // 2, dtype=np.float32), sample_rate=SAMPLE_RATE)
        except Exception as exc:  # pragma: no cover - warmup must never be fatal
            log.debug("Warmup pass failed harmlessly: %s", exc)

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int,
        language: str | None = None,
    ) -> TranscriptResult:
        model = self._load()
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)

        kwargs: dict[str, object] = {"sample_rate": sample_rate}
        language = language if language is not None else self.settings.language
        if language:
            kwargs["language"] = language

        try:
            text = model.recognize(audio, **kwargs)
        except Exception as exc:
            if "language" not in kwargs:
                raise SttError(f"Local transcription failed: {exc}") from exc
            # Only Whisper and Canary take a language hint; the rest reject it.
            log.debug("Retrying without language hint after: %s", exc)
            kwargs.pop("language")
            language = None
            try:
                text = model.recognize(audio, **kwargs)
            except Exception as retry_exc:
                raise SttError(f"Local transcription failed: {retry_exc}") from retry_exc

        return TranscriptResult(
            text=(text or "").strip(),
            backend=self.name,
            model=self.settings.model,
            language=language,
            duration=len(audio) / sample_rate,
        )
