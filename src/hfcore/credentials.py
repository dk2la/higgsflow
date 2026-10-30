"""The Higgsfield API key: parsing, saving and looking it up.

Lookup order (DESIGN.md §4): `HF_KEY="id:secret"`, then `HF_API_KEY` + `HF_API_SECRET`
(the official SDKs' variables), then `<config dir>/credentials.toml` written by
`higgs auth login` with mode 600. No keychain.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ValidationError

from hfcore import config
from hfcore.errors import HiggsError

CREDENTIALS_FILE = "credentials.toml"


class Credentials(BaseModel):
    key_id: str
    key_secret: str

    @property
    def authorization(self) -> str:
        """The value of the `Authorization` header."""
        return f"Key {self.key_id}:{self.key_secret}"


def parse_key(key: str) -> Credentials:
    """Parse `KEY_ID:KEY_SECRET` as shown in the Higgsfield Console."""
    key_id, sep, key_secret = key.strip().partition(":")
    if not sep or not key_id or not key_secret:
        raise HiggsError(
            "The key must look like KEY_ID:KEY_SECRET",
            hint="Copy both parts from console.higgsfield.ai, separated by a colon.",
            code="invalid_key_format",
        )
    return Credentials(key_id=key_id, key_secret=key_secret)


def credentials_path() -> Path:
    return config.config_dir() / CREDENTIALS_FILE


def load_credentials() -> Credentials:
    """Find the API key: env vars first, then the credentials file."""
    if key := os.environ.get("HF_KEY"):
        return parse_key(key)
    key_id, key_secret = os.environ.get("HF_API_KEY"), os.environ.get("HF_API_SECRET")
    if key_id and key_secret:
        return Credentials(key_id=key_id, key_secret=key_secret)
    if key_id or key_secret:
        raise HiggsError(
            "Only one of HF_API_KEY and HF_API_SECRET is set",
            hint="Set both, or set HF_KEY=KEY_ID:KEY_SECRET instead.",
            code="incomplete_key_env",
        )
    path = credentials_path()
    if path.is_file():
        try:
            data = tomllib.loads(path.read_text())
            return Credentials.model_validate(data)
        except (tomllib.TOMLDecodeError, ValidationError):
            raise HiggsError(
                f"Cannot read the key from {path}",
                hint="Run `higgs auth login` to save it again.",
                code="invalid_credentials_file",
            ) from None
    raise HiggsError(
        "No Higgsfield API key configured",
        hint="Run `higgs auth login`, or set HF_KEY=KEY_ID:KEY_SECRET.",
        code="missing_key",
    )


def save_credentials(credentials: Credentials) -> Path:
    """Write the credentials file, readable by the owner only."""
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Strings are written as JSON literals, which are valid TOML basic strings.
    content = (
        f"key_id = {json.dumps(credentials.key_id)}\n"
        f"key_secret = {json.dumps(credentials.key_secret)}\n"
    )
    # Create with 0600 so the secret is never world-readable, even for a moment,
    # and chmod in case the file already existed with a wider mode.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(content)
    path.chmod(0o600)
    return path
