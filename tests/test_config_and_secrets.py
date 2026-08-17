from __future__ import annotations

import pytest
from pydantic import ValidationError

from skydict import secrets
from skydict.config import Settings
from skydict.secrets import MissingCredentialError, env_var_name, get_key, require_key
from skydict.stt.local_onnx import LocalOnnxBackend
from skydict.stt.openai_compat import OpenAICompatBackend
from skydict.stt.registry import build_backend


def test_defaults_target_groq_and_gigaam():
    settings = Settings()

    assert settings.cloud.base_url == "https://api.groq.com/openai/v1"
    assert settings.local.model == "gigaam-v3-e2e-rnnt"
    assert settings.backend == "cloud"


def test_settings_roundtrip_through_disk(tmp_path):
    path = tmp_path / "config.json"
    original = Settings()
    original.backend = "local"
    original.cloud.base_url = "https://api.openai.com/v1"
    original.vad.silence_duration = 2.5
    original.save(path)

    loaded = Settings.load(path)

    assert loaded.backend == "local"
    assert loaded.cloud.base_url == "https://api.openai.com/v1"
    assert loaded.vad.silence_duration == 2.5


def test_load_falls_back_to_defaults_when_absent(tmp_path):
    assert Settings.load(tmp_path / "missing.json").backend == "cloud"


def test_saved_config_never_contains_a_key(tmp_path, monkeypatch):
    monkeypatch.setenv(env_var_name("groq"), "super-secret")
    path = Settings().save(tmp_path / "config.json")

    assert "super-secret" not in path.read_text()


def test_invalid_values_are_rejected(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"vad": {"silence_duration": -1}}')

    with pytest.raises(ValidationError):
        Settings.load(path)


def test_env_var_overrides_keychain(monkeypatch):
    monkeypatch.setenv(env_var_name("groq"), "from-env")
    monkeypatch.setattr(secrets.keyring, "get_password", lambda *a: "from-keychain")

    assert get_key("groq") == "from-env"


def test_keychain_is_used_when_env_is_unset(monkeypatch):
    monkeypatch.delenv(env_var_name("groq"), raising=False)
    monkeypatch.setattr(secrets.keyring, "get_password", lambda *a: "from-keychain")

    assert get_key("groq") == "from-keychain"


def test_locked_keychain_does_not_raise(monkeypatch):
    monkeypatch.delenv(env_var_name("groq"), raising=False)

    def locked(*a):
        raise secrets.KeyringError("keychain locked")

    monkeypatch.setattr(secrets.keyring, "get_password", locked)

    assert get_key("groq") is None
    with pytest.raises(MissingCredentialError):
        require_key("groq")


def test_env_var_name_normalises_dashes():
    assert env_var_name("my-service") == "SKYDICT_MY_SERVICE_API_KEY"


def test_registry_builds_the_configured_backend():
    settings = Settings()

    assert isinstance(build_backend(settings), OpenAICompatBackend)
    assert isinstance(build_backend(settings, "local"), LocalOnnxBackend)

    settings.backend = "local"
    assert isinstance(build_backend(settings), LocalOnnxBackend)


def test_registry_rejects_unknown_backend():
    with pytest.raises(ValueError, match="Unknown backend"):
        build_backend(Settings(), "quantum")


def test_local_backend_does_not_load_the_model_on_construction():
    backend = LocalOnnxBackend(Settings().local)

    assert not backend.is_loaded
    assert backend.model == "gigaam-v3-e2e-rnnt"
