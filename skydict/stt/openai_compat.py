"""Transcription through any OpenAI-compatible ``/v1/audio/transcriptions`` endpoint.

Groq is the reference target, but because the base URL and model come from settings the
same code covers OpenAI itself and self-hosted servers such as vLLM or LocalAI.
"""

from __future__ import annotations

import logging
import time

import httpx
import numpy as np

from ..config import CloudBackendSettings
from ..secrets import require_key
from .base import SttError, TranscriptResult, to_wav_bytes

log = logging.getLogger(__name__)

#: Transient conditions worth retrying; everything else fails immediately.
RETRY_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})


class OpenAICompatBackend:
    name = "cloud"

    def __init__(
        self,
        settings: CloudBackendSettings,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.model = settings.model
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.settings.timeout_seconds)
        return self._client

    def warmup(self) -> None:
        # Resolve the credential now so a missing key surfaces at startup rather than
        # after the user has already dictated a sentence into a doomed request.
        require_key(self.settings.credential_name)
        self._get_client()

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def check_connection(self) -> str:
        """Verify credentials and reachability by listing models. Returns a status line."""
        url = f"{self.settings.base_url.rstrip('/')}/models"
        try:
            response = self._get_client().get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise SttError(f"Could not reach {url}: {exc}") from exc
        if response.status_code == 401:
            raise SttError("Authentication failed — check the API key.")
        response.raise_for_status()
        models = [entry.get("id") for entry in response.json().get("data", [])]
        if self.model not in models:
            return (
                f"Connected, but '{self.model}' was not in the "
                f"model list ({len(models)} models)."
            )
        return f"Connected. Model '{self.model}' is available."

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {require_key(self.settings.credential_name)}"}

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int,
        language: str | None = None,
    ) -> TranscriptResult:
        wav_bytes = to_wav_bytes(audio, sample_rate)
        url = f"{self.settings.base_url.rstrip('/')}/audio/transcriptions"

        data: dict[str, str] = {"model": self.model, "response_format": "json"}
        language = language if language is not None else self.settings.language
        if language:
            data["language"] = language
        if self.settings.prompt:
            data["prompt"] = self.settings.prompt

        payload = self._post_with_retry(
            url,
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data=data,
        )

        return TranscriptResult(
            text=(payload.get("text") or "").strip(),
            backend=self.name,
            model=self.model,
            language=language,
            duration=len(audio) / sample_rate,
            raw=payload,
        )

    def _post_with_retry(self, url: str, *, files: dict, data: dict) -> dict:
        headers = self._headers()
        client = self._get_client()
        last_error: Exception | None = None

        for attempt in range(self.settings.max_retries):
            try:
                response = client.post(url, headers=headers, files=files, data=data)
            except httpx.HTTPError as exc:
                last_error = exc
                log.warning("Request to %s failed (attempt %d): %s", url, attempt + 1, exc)
            else:
                if response.status_code == 200:
                    return response.json()
                if response.status_code == 401:
                    raise SttError("Authentication failed — check the API key.")
                if response.status_code not in RETRY_STATUS:
                    raise SttError(
                        f"Transcription failed with HTTP {response.status_code}: "
                        f"{response.text[:300]}"
                    )
                last_error = SttError(f"HTTP {response.status_code}: {response.text[:300]}")
                log.warning(
                    "Retryable HTTP %d from %s (attempt %d)",
                    response.status_code,
                    url,
                    attempt + 1,
                )
                self._sleep_before_retry(response, attempt)
                continue

            if attempt < self.settings.max_retries - 1:
                time.sleep(2.0**attempt)

        raise SttError(
            f"Transcription failed after {self.settings.max_retries} attempts: {last_error}"
        )

    def _sleep_before_retry(self, response: httpx.Response, attempt: int) -> None:
        if attempt >= self.settings.max_retries - 1:
            return
        delay = 2.0**attempt
        # Honour the server's own backoff hint when it sends one.
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        time.sleep(delay)
