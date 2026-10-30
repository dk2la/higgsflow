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
