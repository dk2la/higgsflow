"""Errors the core raises; the CLI renders them as text or JSON with a non-zero exit."""

from __future__ import annotations


class HiggsError(Exception):
    """A failure the user can act on.

    `code` is a stable machine-readable identifier (part of the `--json` contract),
    `hint` an optional next step for the user.
    """

    code = "error"

    def __init__(self, message: str, *, hint: str | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        if code is not None:
            self.code = code

    def to_dict(self) -> dict[str, str]:
        data = {"code": self.code, "message": self.message}
        if self.hint:
            data["hint"] = self.hint
        return data
