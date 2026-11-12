"""API keys live only in the OS credential store (Windows Credential Manager via keyring)."""

from collections.abc import Collection

import keyring
from keyring.errors import PasswordDeleteError

from core.redact import register_secret

SERVICE = "pet-kit/llm"
# Windows Credential Manager caps the secret at 2560 bytes (1280 UTF-16 chars).
MAX_KEY_LENGTH = 1024
HINT_MIN_LENGTH = 12


class KeyStore:
    def __init__(self, service: str = SERVICE) -> None:
        self._service = service

    def get(self, profile: str) -> str | None:
        value = keyring.get_password(self._service, profile)
        register_secret(value)
        return value

    def set(self, profile: str, key: str) -> None:
        key = key.strip()
        if not key or len(key) > MAX_KEY_LENGTH or any(c.isspace() for c in key):
            raise ValueError("API Key 格式不正确")
        register_secret(key)
        keyring.set_password(self._service, profile, key)

    def delete(self, profile: str) -> None:
        try:
            keyring.delete_password(self._service, profile)
        except PasswordDeleteError:
            pass

    def remap(self, sources: dict[str, str | None], old_names: Collection[str]) -> list[str]:
        """Make each name in `sources` hold the key previously stored under its source name (or none).

        All values are read before anything is written, so swaps (A<->B) and chains (A->B->C) work, and
        a name never inherits a key that belonged to a different, deleted profile. Old names that are not
        targets are cleared. Returns the names whose update failed.
        """
        values = {src: self.get(src) for src in set(sources.values()) if src is not None}
        failed = []
        for name in set(sources) | old_names:
            src = sources.get(name)
            value = values.get(src) if src is not None else None
            try:
                if value:
                    keyring.set_password(self._service, name, value)
                else:
                    self.delete(name)
            except Exception:  # noqa: BLE001 - keyring backends raise assorted errors; report, don't abort
                failed.append(name)
        return failed

    def status(self, profile: str) -> dict:
        value = self.get(profile)
        if not value:
            return {"set": False, "hint": None}
        # Short keys get no hint: revealing 4 chars of a short key leaks too much of it.
        return {"set": True, "hint": "…" + value[-4:] if len(value) >= HINT_MIN_LENGTH else None}
