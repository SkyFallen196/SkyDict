"""API key storage backed by the system keyring.

``keyring`` stores secrets wherever the platform keeps them — the macOS Keychain, Windows
Credential Manager or the freedesktop Secret Service. Keys are looked up there first and
fall back to ``SKYDICT_<NAME>_API_KEY`` so tests and CI can run without touching the
user's real keyring.
"""

from __future__ import annotations

import os

import keyring
from keyring.errors import KeyringError

from . import APP_NAME

SERVICE = APP_NAME


class MissingCredentialError(RuntimeError):
    """Raised when a backend needs an API key that has not been configured yet."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"No API key stored for '{name}'. "
            f"Run `skydict set-key {name}` or set {env_var_name(name)}."
        )
        self.name = name


def env_var_name(name: str) -> str:
    return f"SKYDICT_{name.upper().replace('-', '_')}_API_KEY"


def get_key(name: str) -> str | None:
    """Return the stored key for ``name``, or None if it is not configured."""
    env_value = os.environ.get(env_var_name(name))
    if env_value:
        return env_value
    try:
        return keyring.get_password(SERVICE, name)
    except KeyringError:
        # A locked or unavailable keychain should not be fatal — the env var may still work.
        return None


def require_key(name: str) -> str:
    key = get_key(name)
    if not key:
        raise MissingCredentialError(name)
    return key


def set_key(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)


def delete_key(name: str) -> bool:
    """Remove a stored key. Returns False when there was nothing to delete."""
    try:
        keyring.delete_password(SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        return False
    except KeyringError:
        return False
    return True
