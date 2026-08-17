"""SkyDict — dictation app for macOS."""

from __future__ import annotations

import os
from pathlib import Path

__version__ = "0.1.0"

APP_NAME = "SkyDict"

#: Where speech models are downloaded. Kept in the open in the home directory rather
#: than buried in ~/Library/Caches so it is obvious what SkyDict has downloaded and
#: trivial to reclaim the space.
DEFAULT_MODELS_DIR = Path.home() / "SkyDict_models"


def _configure_model_cache() -> Path:
    """Point Hugging Face at SkyDict's model directory.

    This has to run before ``huggingface_hub`` is first imported: that package resolves
    its cache paths at import time. Every import of it in SkyDict is deliberately lazy
    (inside a function) so this module can win the race.

    An HF_HOME already set in the environment is left alone — someone who has pointed
    their whole machine at another disk means it.
    """
    override = os.environ.get("SKYDICT_MODELS_DIR")
    path = Path(override).expanduser() if override else DEFAULT_MODELS_DIR
    os.environ.setdefault("HF_HOME", str(path))
    return Path(os.environ["HF_HOME"])


#: The directory models actually land in, after the environment has its say.
MODELS_DIR = _configure_model_cache()
