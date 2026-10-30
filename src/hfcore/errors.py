"""Errors the core raises; the CLI renders them as text or JSON with a non-zero exit."""

from __future__ import annotations

from typing import Any


class HiggsError(Exception):
    """A failure the user can act on.

    `code` is a stable machine-readable identifier (part of the `--json` contract),
    `hint` an optional next step for the user. Subclasses add context fields
    (e.g. an HTTP status) by extending `to_dict`.
    """

    code = "error"

    def __init__(self, message: str, *, hint: str | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        if code is not None:
            self.code = code

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            data["hint"] = self.hint
        return data
