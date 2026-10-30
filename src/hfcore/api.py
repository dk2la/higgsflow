"""Own small HTTP client for the Higgsfield API (ADR 0001: no official SDK).

`ApiClient` owns the base URL, the `Authorization` header and error mapping. Every
API call goes through `_call`, so a new one (status, cancel, delete, estimate, upload)
is a short method: pick the method and path, and parse the body into an `ApiModel`.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Self

import httpx
from pydantic import BaseModel, ConfigDict

from hfcore.credentials import Credentials
from hfcore.errors import HiggsError

BASE_URL = "https://api.higgsfield.ai"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# Output files can be large; only the gap between chunks is limited.
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
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


class Estimate(ApiModel):
    """`POST /estimate/{endpoint}`: the exact price of a Request with this body, in USD."""

    usd: Decimal


class RequestStatus(ApiModel):
    """`GET /requests/{id}/status`: the status and, once completed, the output fields.

    Outputs are the other top-level fields (`video`, `images`, `audio`, `audios`, and
    Artifacts such as `zip` or `fbx`), returned by `outputs()`.
    """

    status: str
    request_id: str | None = None
    expires_at: datetime | None = None
    error: str | None = None

    def outputs(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class UploadTarget(ApiModel):
    """Where to `PUT` one file (`POST /files/generate-upload-url`), and its URL afterwards."""

    public_url: str
    upload_url: str
    content_type: str | None = None
    upload_headers: dict[str, str] = {}


class Deleted(ApiModel):
    """The response of `DELETE /requests/{id}`."""

    request_id: str
    status: str = "deleted"


class CustomReference(ApiModel):
    """A Soul ID (`POST /v1/custom-references`, `GET /v1/custom-references/{id}`): a character reference.

    Statuses: `not_ready`, `queued`, `in_progress`, `completed`, `failed`.
    """

    id: str
    status: str
    name: str | None = None
    model_version: str | None = None
    thumbnail_url: str | None = None
    created_at: datetime | None = None
    in_progress_at: datetime | None = None
    fail_reason: str | None = None


_CANCEL_HINT ="Only a queued Request can be canceled; once it finishes, `higgs delete` removes its data."
_DELETE_HINT = "Wait until it finishes, or stop a queued one with `higgs cancel` first."
_NOT_FOUND_HINT = "It does not exist or belongs to another account; check the key in use."


class ApiClient:
    """Talks to the Higgsfield API with one key. Use as a context manager."""

    def __init__(self, credentials: Credentials, *, base_url: str = BASE_URL):
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": credentials.authorization},
            timeout=TIMEOUT,
        )
        # Output URLs point at a CDN: never send them the API key.
        self._files = httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self._http.close()
        self._files.close()

    def submit(self, endpoint: str, params: dict[str, Any], *, idempotency_key: str) -> Submitted:
        """Submit a Request to an Endpoint (`POST /{endpoint}`)."""
        response = self._call(
            "POST", f"/{endpoint}", json=params, headers={"Idempotency-Key": idempotency_key}
        )
        return _parse(Submitted, response)

    def estimate(self, endpoint: str, params: dict[str, Any]) -> Estimate:
        """The exact price of submitting `params` to an Endpoint (`POST /estimate/{endpoint}`); free."""
        return _parse(Estimate, self._call("POST", f"/estimate/{endpoint.strip('/')}", json=params))

    def status(self, request_id: str) -> RequestStatus:
        """The current status and outputs of a Request (`GET /requests/{id}/status`)."""
        return _parse(RequestStatus, self._call("GET", f"/requests/{request_id}/status"))

    def download(self, url: str, path: Path) -> None:
        """Stream an output file to `path`, atomically (a failed download leaves nothing behind)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.part")
        try:
            with self._files.stream("GET", url) as response:
                if response.is_error:
                    raise HiggsError(
                        f"Cannot download {url}: HTTP {response.status_code}",
                        hint="The link may have expired; see `expires_at` in the Request record.",
                        code="download_failed",
                    )
                with tmp.open("wb") as file:
                    for chunk in response.iter_bytes():
                        file.write(chunk)
        except httpx.TransportError as error:
            tmp.unlink(missing_ok=True)
            raise HiggsError(
                f"Cannot download {url}: {error}",
                hint="Check the network connection and try again.",
                code="download_failed",
            ) from None
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, path)
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

    def cancel(self, request_id: str) -> None:
        """Cancel a queued Request (`POST /requests/{id}/cancel`; 202 with an empty body)."""
        self._call_on_request(
            "POST",
            request_id,
            "/cancel",
            # 400: processing has started.
            {400: ("cannot_cancel", "is already in progress and cannot be canceled", _CANCEL_HINT)},
        )

    def delete(self, request_id: str) -> Deleted:
        """Delete a finished Request's data on the API side (`DELETE /requests/{id}`)."""
        response = self._call_on_request(
            "DELETE",
            request_id,
            "",
            {409: ("request_active", "is still queued or in progress and cannot be deleted yet", _DELETE_HINT)},
        )
        return _parse(Deleted, response)

    def create_custom_reference(self, body: dict[str, Any]) -> CustomReference:
        """Start training a Soul ID (`POST /v1/custom-references`); not a Request, no Idempotency-Key.

        Too many trainings at once is a 429 with `Retry-After`; nothing is charged for it.
        """
        return _parse(CustomReference, self._call("POST", "/v1/custom-references", json=body))

    def custom_reference(self, reference_id: str) -> CustomReference:
        """The training status of a Soul ID (`GET /v1/custom-references/{id}`)."""
        return _parse(CustomReference, self._call("GET", f"/v1/custom-references/{reference_id}"))

    def list_models(self) -> Any:
        """The undocumented `GET /models`, as raw JSON: its format is unknown, so callers must tolerate anything."""
        return _json(self._call("GET", "/models"))

    def usage(self, start: datetime, end: datetime, *, timeframe: str = "day") -> Any:
        """Usage analytics of the account (`GET /models/usage`), as raw JSON.

        Documented as `{"time_series": [{"bucket", "results": [{"model", "unit", "quantity",
        "cost", "currency", ...}]}], "total", "page", "limit"}`; analytics, not a billing record.
        `start`..`end` may span at most 31 days. One page of up to 100 buckets is fetched.
        """
        params: dict[str, str | int] = {
            "start": _utc(start),
            "end": _utc(end),
            "timeframe": timeframe,
            "limit": 100,
        }
        return _json(self._call("GET", "/models/usage", params=params))

    def _call_on_request(
        self, method: str, request_id: str, suffix: str, errors: dict[int, tuple[str, str, str]]
    ) -> httpx.Response:
        """A call on one Request (`/requests/{id}{suffix}`).

        A 404 and the action's own statuses in `errors` (status → code, problem, hint)
        become Request-specific errors instead of the generic mapping in `api_error`.
        """
        errors = {404: ("api_request_not_found", "is unknown to the API", _NOT_FOUND_HINT), **errors}
        try:
            return self._call(method, f"/requests/{request_id}{suffix}")
        except ApiError as error:
            if error.status not in errors:
                raise
            code, problem, hint = errors[error.status]
            raise ApiError(
                f"Request {request_id} {problem}",
                status=error.status,
                correlation_id=error.correlation_id,
                hint=hint,
                code=code,
            ) from None

    def _call(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str | int] | None = None,
    ) -> httpx.Response:
        """Send one request; a non-2xx response or a network failure raises a `HiggsError`."""
        try:
            response = self._http.request(method, path, json=json, headers=headers, params=params)
        except httpx.TransportError as error:
            raise HiggsError(
                f"Cannot reach the Higgsfield API: {error}",
                hint="Check the network connection and try again.",
                code="network_error",
            ) from None
        if response.is_error:
            raise api_error(response)
        return response


def _json(response: httpx.Response) -> Any:
    """The body as raw JSON, for responses whose shape callers check themselves."""
    try:
        return response.json()
    except ValueError:
        raise ApiError(
            f"Unexpected response from {response.request.url.path}: not JSON",
            status=response.status_code,
            correlation_id=response.headers.get("X-Correlation-ID"),
            code="invalid_response",
        ) from None


def _utc(moment: datetime) -> str:
    """ISO 8601 in UTC with a `Z`, as the API's examples write times."""
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


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
        retry_after: float | None = None,
    ):
        super().__init__(message, hint=hint, code=code)
        self.status = status
        self.correlation_id = correlation_id
        # Seconds to wait before retrying, from a 429's `Retry-After`.
        self.retry_after = retry_after

    def to_dict(self) -> dict[str, Any]:
        data = {**super().to_dict(), "status": self.status, "correlation_id": self.correlation_id}
        if self.retry_after is not None:
            data["retry_after"] = self.retry_after
        return data


_KEY_HINT = "Create or check the key in console.higgsfield.ai, then run `higgs auth login`."


def api_error(response: httpx.Response) -> ApiError:
    """Map an error response to a specific, actionable `ApiError` (DESIGN.md §9)."""
    status = response.status_code
    api_code, detail = _error_details(response)
    # The Endpoint id, also when the failed call was its estimate.
    path = response.request.url.path.strip("/").removeprefix("estimate/")

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
        retry_after = _retry_after(response)
        wait = f" (retry after {retry_after:g} s)" if retry_after is not None else ""
        code = "queue_full" if api_code == "account_queue_full" else "rate_limited"
        limited = error(code, f"Too many Requests{wait}: {detail}", "Wait for running Requests to finish, then retry.")
        limited.retry_after = retry_after
        return limited
    return error("api_error", f"API error (HTTP {status}): {detail}")


def _retry_after(response: httpx.Response) -> float | None:
    """Seconds from the `Retry-After` header, else from `retry_after_seconds` in the body."""
    candidates: list[Any] = [response.headers.get("Retry-After")]
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("detail"), dict):
        candidates.append(body["detail"].get("retry_after_seconds"))
    for value in candidates:
        try:
            seconds = float(value)
        except (TypeError, ValueError):  # missing, or an HTTP date: fall back to backoff
            continue
        if seconds >= 0:
            return seconds
    return None


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
