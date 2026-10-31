"""`higgs gen` without `--no-wait`: polling, terminal statuses and downloading Generations."""

import itertools
import json
from typing import Any

import httpx
import pytest

# Every test submits, and every submit is preceded by an estimate.
pytestmark = pytest.mark.usefixtures("estimate")

ENDPOINT = "higgsfield-ai/soul/v2/standard"
SUBMIT_URL = f"https://api.higgsfield.ai/{ENDPOINT}"
REQUEST_ID = "d7e6c0f3-6699-4f6c-bb45-2ad7fd9158ff"
STATUS_URL = f"https://api.higgsfield.ai/requests/{REQUEST_ID}/status"

# Response bodies from the docs ("Requests and lifecycle").
SUBMITTED = {
    "status": "queued",
    "request_id": REQUEST_ID,
    "status_url": STATUS_URL,
    "cancel_url": f"https://api.higgsfield.ai/requests/{REQUEST_ID}/cancel",
    "expires_after": 604800,
}
IN_PROGRESS = {"status": "in_progress", "request_id": REQUEST_ID}
COMPLETED_ONE_IMAGE = {
    "status": "completed",
    "request_id": REQUEST_ID,
    "expires_at": "2026-10-31T12:05:12.481Z",
    "images": [{"url": "https://cdn.example.com/image-1.jpg"}],
}

DAY = "generations/2026-10-30"


def status(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=body)


def test_a_completed_request_downloads_its_generation_with_metadata(higgs, project, api_key, respx_mock):
    respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(200, json=SUBMITTED))
    respx_mock.get(STATUS_URL).mock(side_effect=[status(IN_PROGRESS), status(COMPLETED_ONE_IMAGE)])
    respx_mock.get("https://cdn.example.com/image-1.jpg").mock(return_value=httpx.Response(200, content=b"jpeg-1"))

    result = higgs(
        "gen", ENDPOINT, "-p", "prompt=Editorial portrait in soft daylight", "--no-validate", "--json", "--yes"
    )

    assert result.exit_code == 0, result.output
    name = "20261030-1200_soul-v2_t2i_editorial-portrait-in-soft_v01.jpg"
    generation = project / DAY / name
    assert generation.read_bytes() == b"jpeg-1"
    metadata = json.loads((project / DAY / f"{name}.json").read_text())
    assert metadata["request_id"] == REQUEST_ID
    assert metadata["endpoint"] == ENDPOINT
    assert metadata["params"] == {"prompt": "Editorial portrait in soft daylight"}
    assert metadata["prompt"] == "Editorial portrait in soft daylight"
    assert metadata["url"] == "https://cdn.example.com/image-1.jpg"
    assert metadata["expires_at"] == "2026-10-31T12:05:12.481000Z"
    assert metadata["submitted_at"]
    assert metadata["artifacts"] == []

    record = json.loads((project / ".hf" / "requests" / f"{REQUEST_ID}.json").read_text())
    assert record["status"] == "completed"
    assert record["expires_at"] == "2026-10-31T12:05:12.481000Z"

    output = json.loads(result.output)
    assert output["request_id"] == REQUEST_ID
    assert output["generations"][0]["path"] == str(generation)


def mock_completed_image(respx_mock, *statuses: httpx.Response | Exception):
    """Submit → the given status responses → completed with one image, and the image bytes."""
    respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(200, json=SUBMITTED))
    route = respx_mock.get(STATUS_URL).mock(side_effect=[*statuses, status(COMPLETED_ONE_IMAGE)])
    respx_mock.get("https://cdn.example.com/image-1.jpg").mock(return_value=httpx.Response(200, content=b"jpeg-1"))
    return route


def gen(higgs, *args: str):
    return higgs("gen", ENDPOINT, "-p", "prompt=a cat", "--no-validate", "--json", "--yes", *args)


def test_polling_backs_off_from_two_seconds_to_at_most_ten_with_jitter(higgs, project, api_key, respx_mock, clock):
    mock_completed_image(respx_mock, *[status(IN_PROGRESS)] * 12)

    result = gen(higgs)

    assert result.exit_code == 0, result.output
    waits = clock.waits
    assert len(waits) == 12  # one wait after each non-terminal status, none after the last
    assert 2.0 <= waits[0] <= 2.5
    assert all(later > earlier for earlier, later in itertools.pairwise(waits[:5]))  # backing off
    assert all(wait <= 10.0 for wait in waits)
    assert all(wait >= 9.0 for wait in waits[6:])  # reached the cap
    assert len(set(waits[6:])) > 1  # jittered, not a fixed interval


# Error bodies from the docs ("Rate limits", "Errors and retries").
QUEUE_FULL = {
    "detail": {
        "code": "account_queue_full",
        "message": "Too many queued generations for this account",
        "retryable": True,
        "limit": 40,
        "retry_after_seconds": 30,
    }
}
ADMISSION_UNAVAILABLE = {"detail": {"code": "admission_control_unavailable", "message": "Try again", "retryable": True}}


def test_a_429_while_polling_waits_for_retry_after(higgs, project, api_key, respx_mock, clock):
    route = mock_completed_image(respx_mock, httpx.Response(429, json=QUEUE_FULL, headers={"Retry-After": "30"}))

    result = gen(higgs)

    assert result.exit_code == 0, result.output
    assert clock.waits == [30.0]
    assert route.call_count == 2


def test_a_503_while_polling_is_retried_with_backoff(higgs, project, api_key, respx_mock, clock):
    route = mock_completed_image(
        respx_mock,
        httpx.Response(503, json=ADMISSION_UNAVAILABLE),
        httpx.Response(503, json={"detail": "Service Unavailable"}),
    )

    result = gen(higgs)

    assert result.exit_code == 0, result.output
    assert route.call_count == 3
    assert len(clock.waits) == 2
    assert 2.0 <= clock.waits[0] <= 2.5
    assert clock.waits[1] > clock.waits[0]


def test_a_network_failure_while_polling_is_retried(higgs, project, api_key, respx_mock, clock):
    route = mock_completed_image(respx_mock, httpx.ConnectTimeout("timed out"))

    result = gen(higgs)

    assert result.exit_code == 0, result.output
    assert route.call_count == 2


def test_polling_gives_up_after_repeated_errors_and_keeps_the_record(higgs, project, api_key, respx_mock, clock):
    respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(200, json=SUBMITTED))
    respx_mock.get(STATUS_URL).mock(return_value=httpx.Response(503, json=ADMISSION_UNAVAILABLE))

    result = gen(higgs)

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "service_unavailable"
    assert REQUEST_ID in error["hint"]
    assert clock.waits and all(wait <= 10.0 for wait in clock.waits)
    record = json.loads((project / ".hf" / "requests" / f"{REQUEST_ID}.json").read_text())
    assert record["status"] == "queued"


def test_polling_a_full_queue_gives_up_after_eight_checks_and_points_to_sync(higgs, project, api_key, respx_mock, clock):
    respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(200, json=SUBMITTED))
    status = respx_mock.get(STATUS_URL).mock(
        return_value=httpx.Response(429, json=QUEUE_FULL, headers={"Retry-After": "30"})
    )

    result = gen(higgs)

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "queue_full"
    # POLL_ERROR_ATTEMPTS = 8 consecutive failed checks, with a Retry-After wait between them.
    assert status.call_count == 8
    assert clock.waits == [30.0] * 7
    assert REQUEST_ID in error["hint"]
    assert "higgs sync" in error["hint"]
    record = json.loads((project / ".hf" / "requests" / f"{REQUEST_ID}.json").read_text())
    assert record["status"] == "queued"


def test_an_authentication_error_while_polling_is_not_retried(higgs, project, api_key, respx_mock, clock):
    respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(200, json=SUBMITTED))
    route = respx_mock.get(STATUS_URL).mock(return_value=httpx.Response(401, json={"detail": "Invalid credentials"}))

    result = gen(higgs)

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "invalid_key"
    assert route.call_count == 1
    assert clock.waits == []


# Terminal failures from the docs ("Errors and retries", "Webhooks"); canceled has the same shape.
UNSUCCESSFUL: list[tuple[dict[str, Any], str, str]] = [
    ({"status": "failed", "request_id": REQUEST_ID, "error": "Generation failed"}, "request_failed", "failed"),
    ({"status": "nsfw", "request_id": REQUEST_ID, "error": None}, "request_nsfw", "content moderation"),
    ({"status": "canceled", "request_id": REQUEST_ID}, "request_canceled", "canceled"),
]


@pytest.mark.parametrize(("body", "code", "words"), UNSUCCESSFUL)
def test_an_unsuccessful_request_is_recorded_and_downloads_nothing(
    higgs, project, api_key, respx_mock, body, code, words
):
    respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(200, json=SUBMITTED))
    respx_mock.get(STATUS_URL).mock(side_effect=[status(IN_PROGRESS), status(body)])

    result = gen(higgs)

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == code
    assert error["request_id"] == REQUEST_ID
    assert words in error["message"]
    record = json.loads((project / ".hf" / "requests" / f"{REQUEST_ID}.json").read_text())
    assert record["status"] == body["status"]
    assert record["error"] == body.get("error")
    assert not any((project / "generations").iterdir())
    assert len(respx_mock.calls) == 4  # estimate, submit and two status checks, no download


def test_a_failed_request_as_text_says_why(higgs, project, api_key, respx_mock):
    respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(200, json=SUBMITTED))
    respx_mock.get(STATUS_URL).mock(return_value=status(UNSUCCESSFUL[0][0]))

    result = higgs("gen", ENDPOINT, "-p", "prompt=a cat", "--no-validate", "--yes")

    assert result.exit_code == 1
    output = " ".join(result.output.split())
    assert f"Request {REQUEST_ID} failed: Generation failed" in output
    assert "not billed" in output


def test_a_completed_request_as_text_lists_the_downloaded_files(higgs, project, api_key, respx_mock):
    mock_completed_image(respx_mock, status(IN_PROGRESS))

    result = higgs("gen", ENDPOINT, "-p", "prompt=a cat", "--no-validate", "--yes")

    assert result.exit_code == 0, result.output
    assert f"Completed Request {REQUEST_ID}" in result.output
    assert f"{DAY}/20261030-1200_soul-v2_t2i_a-cat_v01.jpg" in result.output


def test_a_failed_download_is_an_error_and_leaves_no_partial_file(higgs, project, api_key, respx_mock):
    respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(200, json=SUBMITTED))
    respx_mock.get(STATUS_URL).mock(return_value=status(COMPLETED_ONE_IMAGE))
    respx_mock.get("https://cdn.example.com/image-1.jpg").mock(return_value=httpx.Response(403))

    result = gen(higgs)

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "download_failed"
    assert not any((project / DAY).iterdir())
    record = json.loads((project / ".hf" / "requests" / f"{REQUEST_ID}.json").read_text())
    assert record["status"] == "completed"
    assert record["downloaded_at"] is None


def test_downloads_do_not_send_the_api_key_to_the_cdn(higgs, project, api_key, respx_mock):
    mock_completed_image(respx_mock)

    gen(higgs)

    download = respx_mock.calls.last.request
    assert download.url == "https://cdn.example.com/image-1.jpg"
    assert "Authorization" not in download.headers


def test_the_metadata_lists_the_inputs_of_the_request(higgs, project, api_key, respx_mock):
    (project / "inputs" / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 32)
    # The upload responses from the docs ("File uploads").
    respx_mock.post("https://api.higgsfield.ai/files/generate-upload-url").mock(
        return_value=httpx.Response(
            200,
            json={
                "public_url": "https://cdn.example.com/input/example.jpeg",
                "upload_url": "https://storage.example.com/presigned-upload-url",
                "content_type": "image/jpeg",
                "upload_headers": {"Content-Type": "image/jpeg"},
            },
        )
    )
    respx_mock.put("https://storage.example.com/presigned-upload-url").mock(return_value=httpx.Response(200))
    mock_completed_image(respx_mock)

    result = gen(higgs, "-p", "image_url=inputs/a.jpg")

    assert result.exit_code == 0, result.output
    metadata = json.loads((project / DAY / "20261030-1200_soul-v2_t2i_a-cat_v01.jpg.json").read_text())
    assert [(item["param"], item["path"], item["public_url"]) for item in metadata["inputs"]] == [
        ("image_url", "inputs/a.jpg", "https://cdn.example.com/input/example.jpeg")
    ]
    assert metadata["params"]["image_url"] == "https://cdn.example.com/input/example.jpeg"
