"""Own small HTTP client for the Higgsfield API (ADR 0001: no official SDK).

`ApiClient` owns the base URL, the `Authorization` header and error mapping. Every
API call goes through `_call`, so a new one (status, cancel, delete, estimate, upload)
is a short method: pick the method and path, and parse the body into an `ApiModel`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self

import httpx
from pydantic import BaseModel, ConfigDict

from hfcore.credentials import Credentials
from hfcore.errors import HiggsError

BASE_URL = "https://api.higgsfield.ai"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# A video Input may be up to a few hundred MiB; allow a slow uplink between chunks.
UPLOAD_TIMEOUT = httpx.Timeout(300.0, connect=10.0)


class ApiModel(BaseModel):
    """A response body. Unknown fields are kept; `correlation_id` is the `X-Correlation-ID` header."""

    model_config = ConfigDict(extra="allow")

    correlation_id: str | None = None


class Submitted(ApiModel):
    """The acceptance receipt of `POST /{endpoint}`."""

    request_id: str
    status: str = "queued"
    status_url: str | None = None
    cancel_url: str | None = None
    expires_after: int | None = None


class UploadTarget(ApiModel):
    """Where to `PUT` one file (`POST /files/generate-upload-url`), and its URL afterwards."""

    public_url: str
    upload_url: str
    content_type: str | None = None
    upload_headers: dict[str, str] = {}


class ApiClient:
    """Talks to the Higgsfield API with one key. Use as a context manager."""

    def __init__(self, credentials: Credentials, *, base_url: str = BASE_URL):
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": credentials.authorization},
            timeout=TIMEOUT,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self._http.close()

    def submit(self, endpoint: str, params: dict[str, Any], *, idempotency_key: str) -> Submitted:
        """Submit a Request to an Endpoint (`POST /{endpoint}`)."""
        response = self._call(
            "POST", f"/{endpoint}", json=params, headers={"Idempotency-Key": idempotency_key}
        )
        return _parse(Submitted, response)

    def create_upload(self, content_type: str) -> UploadTarget:
        """Get a presigned upload URL for one file (`POST /files/generate-upload-url`)."""
        response = self._call("POST", "/files/generate-upload-url", json={"content_type": content_type})
        return _parse(UploadTarget, response)

    def put_file(self, target: UploadTarget, path: Path, *, content_type: str) -> None:
        """Send a file to its presigned URL with the headers the API asked for.

        A separate client on purpose: the API key must never reach the storage host.
        The file is streamed with a `Content-Length` (presigned PUTs reject chunked bodies).
        """
        headers = target.upload_headers or {"Content-Type": content_type}
        try:
            with httpx.Client(timeout=UPLOAD_TIMEOUT) as http, path.open("rb") as file:
                response = http.put(target.upload_url, content=file, headers=headers)
        except httpx.TransportError as error:
            raise HiggsError(
                f"Cannot upload {path.name}: {error}",
                hint="Check the network connection and try again.",
                code="network_error",
            ) from None
        if response.is_error:
            raise HiggsError(
                f"Uploading {path.name} failed (HTTP {response.status_code}): {response.text.strip()[:200]}",
                hint="Try again; a new upload URL is requested every time.",
                code="upload_failed",
            )

    def _call(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Send one request; a non-2xx response or a network failure raises a `HiggsError`."""
        try:
            response = self._http.request(method, path, json=json, headers=headers)
        except httpx.TransportError as error:
            raise HiggsError(
                f"Cannot reach the Higgsfield API: {error}",
                hint="Check the network connection and try again.",
                code="network_error",
            ) from None
        if response.is_error:
            raise api_error(response)
        return response


def _parse[M: ApiModel](model: type[M], response: httpx.Response) -> M:
    correlation_id = response.headers.get("X-Correlation-ID")
    try:
        return model.model_validate({**response.json(), "correlation_id": correlation_id})
    except (ValueError, TypeError) as error:  # not JSON, not an object, or missing fields
        raise ApiError(
            f"Unexpected response from {response.request.url.path}: {error}",
            status=response.status_code,
            correlation_id=correlation_id,
            code="invalid_response",
        ) from None


class ApiError(HiggsError):
    """An error response from the API, with its HTTP status and `X-Correlation-ID`."""

    code = "api_error"

    def __init__(
        self,
        message: str,
        *,
        status: int,
        correlation_id: str | None,
        hint: str | None = None,
        code: str | None = None,
    ):
        super().__init__(message, hint=hint, code=code)
        self.status = status
        self.correlation_id = correlation_id

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "status": self.status, "correlation_id": self.correlation_id}


_KEY_HINT = "Create or check the key in console.higgsfield.ai, then run `higgs auth login`."


def api_error(response: httpx.Response) -> ApiError:
    """Map an error response to a specific, actionable `ApiError` (DESIGN.md §9)."""
    status = response.status_code
    api_code, detail = _error_details(response)
    path = response.request.url.path.strip("/")

    def error(code: str, message: str, hint: str | None = None) -> ApiError:
        return ApiError(
            message,
            status=status,
            correlation_id=response.headers.get("X-Correlation-ID"),
            hint=hint,
            code=code,
        )

    if status == 401:
        return error("invalid_key", f"The API key was rejected: {detail}", _KEY_HINT)
    if status == 403 and api_code == "api_key_expired":
        return error("api_key_expired", f"The API key has expired: {detail}", _KEY_HINT)
    if status == 403 and api_code == "api_key_disabled":
        return error("api_key_disabled", f"The API key is disabled: {detail}", _KEY_HINT)
    if status == 403:
        return error(
            "insufficient_funds",
            f"Not enough balance: {detail}",
            "Add funds in console.higgsfield.ai (API billing is separate from higgsfield.ai credits).",
        )
    if status == 503 and api_code == "admission_control_unavailable":
        return error("service_unavailable", f"The API cannot accept Requests right now: {detail}", "Try again shortly.")
    if status in (404, 423, 503):
        hint = (
            "Check the Endpoint id; a wrong id costs nothing."
            if status == 404
            else "The model may be temporarily blocked or not ready; try again later."
        )
        return error("model_unavailable", f"Endpoint {path} is unavailable for this account: {detail}", hint)
    if status == 422 and "idempotency" in detail.lower():
        return error(
            "idempotency_conflict",
            f"Idempotency conflict: {detail}",
            "The same Idempotency-Key was reused with different parameters; a new submit uses a new key.",
        )
    if status == 422:
        return error("invalid_params", f"The API rejected the parameters: {detail}")
    if status == 429:
        retry_after = response.headers.get("Retry-After")
        wait = f" (retry after {retry_after} s)" if retry_after else ""
        code = "queue_full" if api_code == "account_queue_full" else "rate_limited"
        return error(code, f"Too many Requests{wait}: {detail}", "Wait for running Requests to finish, then retry.")
    return error("api_error", f"API error (HTTP {status}): {detail}")


def _error_details(response: httpx.Response) -> tuple[str | None, str]:
    """The machine code (if any) and a readable detail from a FastAPI-style error body.

    Seen shapes: `{"detail": "..."}`, `{"code": "...", "detail": "..."}`,
    `{"detail": {"code": "...", "message": "..."}}` and a validation list in `detail`.
    """
    try:
        body = response.json()
    except ValueError:
        return None, response.text.strip() or response.reason_phrase
    if not isinstance(body, dict):
        return None, str(body)
    detail = body.get("detail")
    code = body.get("code")
    if isinstance(detail, dict):
        code = code or detail.get("code")
        text = str(detail.get("message") or detail)
    elif isinstance(detail, list):
        text = "; ".join(_validation_issue(item) for item in detail)
    else:
        text = str(detail if detail is not None else body)
    return (str(code) if code else None), text


def _validation_issue(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item)
    # `loc` starts with where the field lives ("body"); the rest is the parameter path.
    loc = [str(part) for part in item.get("loc", []) if part != "body"]
    message = str(item.get("msg", item))
    return f"{'.'.join(loc)}: {message}" if loc else message
