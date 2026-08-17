"""Backend selection from settings."""

from __future__ import annotations

from ..config import BackendName, Settings
from .base import SttBackend
from .local_onnx import LocalOnnxBackend
from .openai_compat import OpenAICompatBackend


def build_backend(settings: Settings, name: BackendName | None = None) -> SttBackend:
    """Construct the backend named in settings, or the override passed in ``name``."""
    name = name or settings.backend
    if name == "cloud":
        return OpenAICompatBackend(settings.cloud)
    if name == "local":
        return LocalOnnxBackend(settings.local)
    raise ValueError(f"Unknown backend: {name!r} (expected 'cloud' or 'local')")
