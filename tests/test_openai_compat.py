from __future__ import annotations

import io
import wave

import httpx
import pytest

from skydict.secrets import MissingCredentialError, env_var_name
from skydict.stt.base import SttError
from skydict.stt.openai_compat import OpenAICompatBackend

URL = "https://api.example.test/v1/audio/transcriptions"


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    """Retries are exercised for behaviour, not timing — skip the actual backoff waits."""
    monkeypatch.setattr("skydict.stt.openai_compat.time.sleep", lambda _: None)


def test_transcribe_returns_text(httpx_mock, cloud_settings, tone, api_key):
    httpx_mock.add_response(url=URL, json={"text": "  привет мир  "})

    result = OpenAICompatBackend(cloud_settings).transcribe(tone, 16_000)

    assert result.text == "привет мир"
    assert result.backend == "cloud"
    assert result.model == "whisper-large-v3-turbo"
    assert result.language == "ru"
    assert result.duration == pytest.approx(1.0)


def test_request_carries_auth_model_and_wav(httpx_mock, cloud_settings, tone, api_key):
    httpx_mock.add_response(url=URL, json={"text": "ok"})

    OpenAICompatBackend(cloud_settings).transcribe(tone, 16_000)

    request = httpx_mock.get_requests()[0]
    assert request.headers["authorization"] == f"Bearer {api_key}"

    body = request.read()
    assert b'name="model"' in body
    assert b"whisper-large-v3-turbo" in body
    assert b'name="language"' in body
    assert b"audio.wav" in body

    # The uploaded payload must be a real WAV, not raw floats.
    start = body.index(b"RIFF")
    with wave.open(io.BytesIO(body[start:]), "rb") as wav:
        assert wav.getframerate() == 16_000
        assert wav.getnchannels() == 1


def test_language_override_beats_settings(httpx_mock, cloud_settings, tone, api_key):
    httpx_mock.add_response(url=URL, json={"text": "ok"})

    OpenAICompatBackend(cloud_settings).transcribe(tone, 16_000, language="en")

    assert b"\r\n\r\nen\r\n" in httpx_mock.get_requests()[0].read()


def test_retries_then_succeeds_on_429(httpx_mock, cloud_settings, tone, api_key):
    httpx_mock.add_response(url=URL, status_code=429, text="rate limited")
    httpx_mock.add_response(url=URL, json={"text": "recovered"})

    result = OpenAICompatBackend(cloud_settings).transcribe(tone, 16_000)

    assert result.text == "recovered"
    assert len(httpx_mock.get_requests()) == 2


def test_gives_up_after_max_retries(httpx_mock, cloud_settings, tone, api_key):
    for _ in range(cloud_settings.max_retries):
        httpx_mock.add_response(url=URL, status_code=503, text="unavailable")

    with pytest.raises(SttError, match="after 3 attempts"):
        OpenAICompatBackend(cloud_settings).transcribe(tone, 16_000)

    assert len(httpx_mock.get_requests()) == cloud_settings.max_retries


def test_auth_failure_is_not_retried(httpx_mock, cloud_settings, tone, api_key):
    httpx_mock.add_response(url=URL, status_code=401, text="bad key")

    with pytest.raises(SttError, match="Authentication failed"):
        OpenAICompatBackend(cloud_settings).transcribe(tone, 16_000)

    assert len(httpx_mock.get_requests()) == 1


def test_client_error_is_not_retried(httpx_mock, cloud_settings, tone, api_key):
    httpx_mock.add_response(url=URL, status_code=400, text="bad request")

    with pytest.raises(SttError, match="HTTP 400"):
        OpenAICompatBackend(cloud_settings).transcribe(tone, 16_000)

    assert len(httpx_mock.get_requests()) == 1


def test_network_error_is_retried(httpx_mock, cloud_settings, tone, api_key):
    httpx_mock.add_exception(httpx.ConnectError("no route"), url=URL)
    httpx_mock.add_response(url=URL, json={"text": "back online"})

    result = OpenAICompatBackend(cloud_settings).transcribe(tone, 16_000)

    assert result.text == "back online"


def test_missing_credential_is_reported_clearly(cloud_settings, tone, monkeypatch):
    monkeypatch.delenv(env_var_name("groq"), raising=False)
    monkeypatch.setattr("skydict.secrets.keyring.get_password", lambda *a: None)

    with pytest.raises(MissingCredentialError, match="skydict set-key groq"):
        OpenAICompatBackend(cloud_settings).transcribe(tone, 16_000)


def test_warmup_fails_fast_on_a_missing_credential(cloud_settings, monkeypatch):
    """Otherwise the user only finds out after dictating a sentence into a doomed request."""
    monkeypatch.delenv(env_var_name("groq"), raising=False)
    monkeypatch.setattr("skydict.secrets.keyring.get_password", lambda *a: None)

    with pytest.raises(MissingCredentialError):
        OpenAICompatBackend(cloud_settings).warmup()


def test_warmup_succeeds_when_the_key_is_present(cloud_settings, api_key):
    OpenAICompatBackend(cloud_settings).warmup()


def test_check_connection_reports_missing_model(httpx_mock, cloud_settings, api_key):
    httpx_mock.add_response(
        url="https://api.example.test/v1/models",
        json={"data": [{"id": "some-other-model"}]},
    )

    message = OpenAICompatBackend(cloud_settings).check_connection()

    assert "was not in the model list" in message


def test_check_connection_confirms_available_model(httpx_mock, cloud_settings, api_key):
    httpx_mock.add_response(
        url="https://api.example.test/v1/models",
        json={"data": [{"id": "whisper-large-v3-turbo"}]},
    )

    assert "is available" in OpenAICompatBackend(cloud_settings).check_connection()
