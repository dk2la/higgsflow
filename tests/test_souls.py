"""`higgs soul train|list|status` and `--soul`: Soul IDs, account-scoped and kept in `<config dir>/souls.toml`.

The response shapes come from the docs (Soul ID → Create character): a character reference
with `id`, `name`, `model_version`, `status`, `thumbnail_url`, `created_at`,
`in_progress_at` and `fail_reason`; statuses `not_ready`, `queued`, `in_progress`,
`completed`, `failed`.
"""

import itertools
from datetime import UTC, datetime
import json
import tomllib
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

API = "https://api.higgsfield.ai"
CREATE_URL = f"{API}/v1/custom-references"
SOUL_ID = "3eb1d5a2-7f3c-4c1e-9f0a-2b6d8c4e1a90"
UPLOAD_URL_ENDPOINT = f"{API}/files/generate-upload-url"
STORAGE_URL = "https://storage.example.com/presigned-upload-url"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 16


def reference(status: str, *, soul_id: str = SOUL_ID, name: str = "alice", version: str = "v2", **extra: Any) -> dict[str, Any]:
    """A character reference as the API returns it."""
    return {
        "id": soul_id,
        "name": name,
        "model_version": version,
        "status": status,
        "thumbnail_url": None,
        "created_at": "2026-10-30T12:00:00Z",
        "in_progress_at": None,
        "fail_reason": None,
        **extra,
    }


def mock_uploads(respx_mock: respx.MockRouter) -> respx.Route:
    """Every upload succeeds; the n-th gets the public URL https://cdn.example.com/upload-n."""
    counter = itertools.count(1)
    respx_mock.put(STORAGE_URL).mock(return_value=httpx.Response(200))

    def upload_url(request: httpx.Request) -> httpx.Response:
        content_type = json.loads(request.content)["content_type"]
        return httpx.Response(
            200,
            json={
                "public_url": f"https://cdn.example.com/upload-{next(counter)}",
                "upload_url": STORAGE_URL,
                "content_type": content_type,
                "upload_headers": {"Content-Type": content_type},
            },
        )

    return respx_mock.post(UPLOAD_URL_ENDPOINT).mock(side_effect=upload_url)


def status_route(respx_mock: respx.MockRouter, *statuses: str, soul_id: str = SOUL_ID, **extra: Any) -> respx.Route:
    return respx_mock.get(f"{CREATE_URL}/{soul_id}").mock(
        side_effect=[httpx.Response(200, json=reference(status, soul_id=soul_id, **extra)) for status in statuses]
    )


@pytest.fixture
def photos(project: Path) -> list[str]:
    (project / "inputs" / "front.jpg").write_bytes(JPEG)
    (project / "inputs" / "side.png").write_bytes(PNG)
    return ["inputs/front.jpg", "inputs/side.png"]


def souls_toml(config_dir: Path) -> dict[str, Any]:
    return tomllib.loads((config_dir / "souls.toml").read_text())


def test_training_uploads_the_images_registers_the_soul_and_polls_to_completion(
    higgs, project, photos, api_key, respx_mock, config_dir, clock
):
    mock_uploads(respx_mock)
    create = respx_mock.post(CREATE_URL).mock(return_value=httpx.Response(200, json=reference("not_ready")))
    polls = status_route(respx_mock, "queued", "in_progress", "completed", thumbnail_url="https://cdn.example.com/t.webp")

    result = higgs("soul", "train", "alice", *photos, "--version", "v2", "--json")

    assert result.exit_code == 0, result.output
    assert create.calls.last.request.headers["Authorization"] == api_key
    assert json.loads(create.calls.last.request.content) == {
        "name": "alice",
        "model_version": "v2",
        "input_images": [
            {"type": "image_url", "image_url": "https://cdn.example.com/upload-1"},
            {"type": "image_url", "image_url": "https://cdn.example.com/upload-2"},
        ],
    }
    assert polls.call_count == 3
    output = json.loads(result.output)
    assert output["soul"]["name"] == "alice"
    assert output["soul"]["id"] == SOUL_ID
    assert output["soul"]["status"] == "completed"
    assert output["path"] == str(config_dir / "souls.toml")
    assert souls_toml(config_dir)["souls"]["alice"] == {
        "id": SOUL_ID,
        "version": "v2",
        "status": "completed",
        "images": 2,
        "project": "demo",
        "created_at": datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
        "updated_at": clock.current,
        "thumbnail_url": "https://cdn.example.com/t.webp",
    }
    assert clock.waits  # it waited between polls


def test_the_soul_is_registered_as_soon_as_the_training_starts_so_a_failed_wait_loses_nothing(
    higgs, project, photos, api_key, respx_mock, config_dir
):
    mock_uploads(respx_mock)
    respx_mock.post(CREATE_URL).mock(return_value=httpx.Response(200, json=reference("not_ready")))
    respx_mock.get(f"{CREATE_URL}/{SOUL_ID}").mock(side_effect=httpx.ConnectError("connection dropped"))

    result = higgs("soul", "train", "alice", *photos, "--json")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "network_error"
    assert "higgs soul status alice" in error["hint"]
    registered = souls_toml(config_dir)["souls"]["alice"]
    assert (registered["id"], registered["status"]) == (SOUL_ID, "not_ready")


def test_no_wait_returns_after_the_start_and_soul_status_reports_progress_later(
    higgs, project, photos, api_key, respx_mock, config_dir, clock
):
    mock_uploads(respx_mock)
    respx_mock.post(CREATE_URL).mock(return_value=httpx.Response(200, json=reference("not_ready")))
    polls = status_route(respx_mock, "in_progress", "completed")

    started = higgs("soul", "train", "alice", *photos, "--no-wait", "--json")

    assert started.exit_code == 0, started.output
    assert json.loads(started.output)["soul"]["status"] == "not_ready"
    assert polls.call_count == 0

    clock.advance(minutes=2)
    progress = higgs("soul", "status", "alice", "--json")

    assert progress.exit_code == 0, progress.output
    assert json.loads(progress.output)["soul"]["status"] == "in_progress"
    assert souls_toml(config_dir)["souls"]["alice"]["status"] == "in_progress"
    assert souls_toml(config_dir)["souls"]["alice"]["updated_at"] == clock.current

    done = higgs("soul", "status", "alice", "--json")

    assert json.loads(done.output)["soul"]["status"] == "completed"
    assert souls_toml(config_dir)["souls"]["alice"]["status"] == "completed"
    assert polls.call_count == 2


@pytest.mark.parametrize("count", [0, 101])
def test_fewer_than_1_or_more_than_100_images_fail_before_any_upload(higgs, project, api_key, respx_mock, config_dir, count):
    for index in range(count):
        (project / "inputs" / f"{index}.jpg").write_bytes(JPEG)
    paths = [f"inputs/{index}.jpg" for index in range(count)]

    result = higgs("soul", "train", "alice", *paths, "--json")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "soul_image_count"
    assert f"got {count}" in error["message"]
    assert not respx_mock.calls
    assert not (config_dir / "souls.toml").exists()
    assert not (project / ".hf" / "uploads.json").exists()


def test_training_is_not_a_request_and_not_in_the_spend(higgs, project, photos, api_key, respx_mock):
    mock_uploads(respx_mock)
    respx_mock.post(CREATE_URL).mock(return_value=httpx.Response(200, json=reference("not_ready")))
    status_route(respx_mock, "completed")
    respx_mock.get(f"{API}/models/usage").mock(
        return_value=httpx.Response(200, json={"time_series": [], "total": 0, "page": 1, "limit": 100})
    )

    result = higgs("soul", "train", "alice", *photos, "--json")

    assert result.exit_code == 0, result.output
    assert list((project / ".hf" / "requests").iterdir()) == []
    assert json.loads(higgs("list", "--json").output)["requests"] == []
    local = json.loads(higgs("usage", "--json").output)["project"]
    assert (local["spend_usd"], local["requests"]) == ("0", 0)


def too_many_trainings(retry_after: str) -> httpx.Response:
    # Docs "Rate limits" → Endpoint-specific limits.
    return httpx.Response(
        429,
        headers={"Retry-After": retry_after},
        json={"detail": "Maximum number of concurrent requests (2) has been reached"},
    )


def test_too_many_trainings_at_once_waits_for_retry_after_and_starts_again(
    higgs, project, photos, api_key, respx_mock, config_dir, clock
):
    mock_uploads(respx_mock)
    create = respx_mock.post(CREATE_URL).mock(
        side_effect=[too_many_trainings("30"), too_many_trainings("45"), httpx.Response(200, json=reference("queued"))]
    )

    result = higgs("soul", "train", "alice", *photos, "--no-wait", "--json")

    assert result.exit_code == 0, result.output
    assert create.call_count == 3
    assert clock.waits == [30.0, 45.0]
    assert souls_toml(config_dir)["souls"]["alice"]["status"] == "queued"


def test_a_failed_create_is_not_retried_and_registers_nothing(higgs, project, photos, api_key, respx_mock, config_dir):
    mock_uploads(respx_mock)
    create = respx_mock.post(CREATE_URL).mock(return_value=httpx.Response(500, json={"detail": "boom"}))

    result = higgs("soul", "train", "alice", *photos, "--json")

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["status"] == 500
    # Creating a Soul ID has no Idempotency-Key: retrying an ambiguous failure could train it twice.
    assert create.call_count == 1
    assert not (config_dir / "souls.toml").exists()


OTHER_ID = "9f6c0b1e-2d4a-4e8b-8c3f-7a5d1e0b2c44"


def train(higgs, respx_mock, name: str, *, version: str = "v2", status: str = "completed", soul_id: str = SOUL_ID, args=()):
    """Train a Soul ID through the CLI (images uploaded, created, polled to `status`)."""
    mock_uploads(respx_mock)
    respx_mock.post(CREATE_URL).mock(
        return_value=httpx.Response(200, json=reference("not_ready", soul_id=soul_id, name=name, version=version))
    )
    respx_mock.get(f"{CREATE_URL}/{soul_id}").mock(
        return_value=httpx.Response(200, json=reference(status, soul_id=soul_id, name=name, version=version))
    )
    return higgs("soul", "train", name, "inputs/front.jpg", "--version", version, "--json", *args)


def test_a_taken_name_fails_before_any_upload_unless_forced(higgs, project, photos, api_key, respx_mock, config_dir):
    assert train(higgs, respx_mock, "alice").exit_code == 0
    respx_mock.reset()

    taken = higgs("soul", "train", "alice", *photos, "--json")

    assert taken.exit_code == 1
    error = json.loads(taken.output)["error"]
    assert error["code"] == "soul_exists"
    assert "--force" in error["hint"]
    assert not respx_mock.calls

    forced = train(higgs, respx_mock, "alice", version="cinema", soul_id=OTHER_ID, args=["--force"])

    assert forced.exit_code == 0, forced.output
    registered = souls_toml(config_dir)["souls"]["alice"]
    assert (registered["id"], registered["version"]) == (OTHER_ID, "cinema")
