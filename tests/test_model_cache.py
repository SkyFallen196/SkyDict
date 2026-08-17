"""Where speech models get downloaded."""

from __future__ import annotations

from pathlib import Path

import skydict
from skydict.config import directory_size


def call_configure(monkeypatch, *, skydict_dir=None, hf_home=None) -> Path:
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.delenv("SKYDICT_MODELS_DIR", raising=False)
    if skydict_dir:
        monkeypatch.setenv("SKYDICT_MODELS_DIR", skydict_dir)
    if hf_home:
        monkeypatch.setenv("HF_HOME", hf_home)
    return skydict._configure_model_cache()


def test_models_default_to_the_home_directory(monkeypatch):
    assert call_configure(monkeypatch) == Path.home() / "SkyDict_models"


def test_hf_home_is_set_so_huggingface_follows(monkeypatch):
    """huggingface_hub resolves its cache at import time, so the variable is what counts."""
    import os

    call_configure(monkeypatch)

    assert os.environ["HF_HOME"] == str(Path.home() / "SkyDict_models")


def test_skydict_models_dir_overrides_the_default(monkeypatch, tmp_path):
    assert call_configure(monkeypatch, skydict_dir=str(tmp_path / "elsewhere")) == (
        tmp_path / "elsewhere"
    )


def test_an_existing_hf_home_is_left_alone(monkeypatch, tmp_path):
    """Someone who pointed their whole machine at another disk means it."""
    existing = tmp_path / "global-hf"

    assert call_configure(monkeypatch, hf_home=str(existing)) == existing


def test_tilde_is_expanded(monkeypatch):
    assert call_configure(monkeypatch, skydict_dir="~/somewhere") == Path.home() / "somewhere"


def test_size_of_a_missing_directory_is_zero(tmp_path):
    assert directory_size(tmp_path / "not-there") == 0


def test_size_counts_files(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 1000)
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.bin").write_bytes(b"y" * 500)

    assert directory_size(tmp_path) == 1500


def test_size_does_not_double_count_the_hugging_face_layout(tmp_path):
    """HF stores each file once in blobs and links to it from snapshots; following both
    would report every model at twice its size."""
    blobs = tmp_path / "blobs"
    snapshots = tmp_path / "snapshots"
    blobs.mkdir()
    snapshots.mkdir()
    (blobs / "abc123").write_bytes(b"z" * 2000)
    (snapshots / "model.onnx").symlink_to(blobs / "abc123")

    assert directory_size(tmp_path) == 2000
