"""`higgs soul train|list|status` and `--soul`: Soul IDs, account-scoped and kept in `<config dir>/souls.toml`.

The response shapes come from the docs (Soul ID → Create character): a character reference
with `id`, `name`, `model_version`, `status`, `thumbnail_url`, `created_at`,
`in_progress_at` and `fail_reason`; statuses `not_ready`, `queued`, `in_progress`,
`completed`, `failed`.
"""

import itertools
import json
import tomllib
from datetime import UTC, datetime
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
    hf = project / ".hf"
    before = {path: path.read_bytes() for path in project.rglob("*") if path.is_file()}

    result = higgs("soul", "train", "alice", *photos, "--json")

    assert result.exit_code == 0, result.output
    # The training images' upload cache is the only file written anywhere in the Project.
    after = {path: path.read_bytes() for path in project.rglob("*") if path.is_file()}
    assert sorted(path.relative_to(project).as_posix() for path in after.keys() - before.keys()) == [
        ".hf/uploads.json"
    ]
    assert all(after[path] == content for path, content in before.items())
    assert list((hf / "requests").iterdir()) == []
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


def test_polling_waits_for_retry_after_on_a_429(higgs, project, photos, api_key, respx_mock, clock):
    mock_uploads(respx_mock)
    respx_mock.post(CREATE_URL).mock(return_value=httpx.Response(200, json=reference("not_ready")))
    respx_mock.get(f"{CREATE_URL}/{SOUL_ID}").mock(
        side_effect=[too_many_trainings("7"), httpx.Response(200, json=reference("completed"))]
    )

    result = higgs("soul", "train", "alice", *photos, "--json")

    assert result.exit_code == 0, result.output
    assert clock.waits == [7.0]


def test_training_as_text_says_where_the_soul_id_is_and_that_its_price_is_unknown(
    higgs, project, photos, api_key, respx_mock, config_dir
):
    mock_uploads(respx_mock)
    respx_mock.post(CREATE_URL).mock(return_value=httpx.Response(200, json=reference("not_ready")))
    status_route(respx_mock, "completed")

    result = higgs("soul", "train", "alice", *photos)

    assert result.exit_code == 0, result.output
    assert "Soul ID alice (v2): completed" in result.output
    assert "--soul alice" in result.output
    assert "price" in result.output and "not estimated" in result.output


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


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (["alice", "inputs/front.jpg", "--version", "v3"], "invalid_soul_version"),
        (["two words", "inputs/front.jpg"], "invalid_soul_name"),
        (["a" * 101, "inputs/front.jpg"], "invalid_soul_name"),
        (["alice", "inputs/front.jpg", "inputs/clip.mp4"], "unsupported_input_type"),
        (["alice", "inputs/front.jpg", "inputs/missing.jpg"], "input_not_found"),
        (["alice", "inputs/front.jpg", "front"], "soul_image_not_a_file"),
    ],
)
def test_bad_arguments_fail_before_any_upload(higgs, project, photos, api_key, respx_mock, config_dir, args, code):
    (project / "inputs" / "clip.mp4").write_bytes(MP4)

    result = higgs("soul", "train", *args, "--json")

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == code
    assert not respx_mock.calls
    assert not (config_dir / "souls.toml").exists()


def test_a_failed_training_fails_the_command_and_is_recorded(higgs, project, photos, api_key, respx_mock, config_dir):
    mock_uploads(respx_mock)
    respx_mock.post(CREATE_URL).mock(return_value=httpx.Response(200, json=reference("not_ready")))
    status_route(respx_mock, "in_progress", "failed", fail_reason="No face detected")

    result = higgs("soul", "train", "alice", *photos, "--json")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "soul_training_failed"
    assert "No face detected" in error["message"]
    registered = souls_toml(config_dir)["souls"]["alice"]
    assert (registered["status"], registered["fail_reason"]) == ("failed", "No face detected")


def test_any_text_from_the_api_survives_souls_toml(higgs, project, photos, api_key, respx_mock, config_dir):
    reason = 'Bad "photo"\nC:\\path — лицо не найдено \U0001f642\x7f'
    mock_uploads(respx_mock)
    respx_mock.post(CREATE_URL).mock(return_value=httpx.Response(200, json=reference("not_ready")))
    status_route(respx_mock, "failed", fail_reason=reason)

    higgs("soul", "train", "alice", *photos, "--json")

    assert souls_toml(config_dir)["souls"]["alice"]["fail_reason"] == reason
    assert json.loads(higgs("soul", "list", "--json").output)["souls"][0]["fail_reason"] == reason


@pytest.mark.parametrize("force", [[], ["--force"]])
def test_an_unreadable_souls_toml_fails_before_training_and_is_never_overwritten(
    higgs, project, photos, api_key, respx_mock, config_dir, force
):
    (config_dir / "souls.toml").write_text("[souls.alice\nid = ")

    result = higgs("soul", "train", "bob", *photos, "--json", *force)

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "invalid_souls_file"
    assert (config_dir / "souls.toml").read_text() == "[souls.alice\nid = "
    assert not respx_mock.calls


def test_soul_list_shows_the_registered_soul_ids_without_calling_the_api(
    higgs, project, photos, api_key, respx_mock, monkeypatch, tmp_path
):
    assert train(higgs, respx_mock, "alice").exit_code == 0
    assert train(higgs, respx_mock, "bob", version="cinema", soul_id=OTHER_ID, args=["--no-wait"]).exit_code == 0
    respx_mock.reset()
    monkeypatch.chdir(tmp_path)  # account-scoped: works outside any Project

    result = higgs("soul", "list", "--json")

    assert result.exit_code == 0, result.output
    listed = [
        (soul["name"], soul["version"], soul["status"], soul["id"]) for soul in json.loads(result.output)["souls"]
    ]
    assert listed == [("alice", "v2", "completed", SOUL_ID), ("bob", "cinema", "not_ready", OTHER_ID)]
    assert not respx_mock.calls

    text = higgs("soul", "list")

    assert "alice" in text.output and "cinema" in text.output and OTHER_ID in text.output


def submitted(request_id: str) -> httpx.Response:
    return httpx.Response(200, json={"status": "queued", "request_id": request_id})


def body(route: respx.Route) -> Any:
    return json.loads(route.calls.last.request.content)


@pytest.mark.parametrize(
    ("model", "version", "endpoint"),
    [
        ("soul", "v1", "higgsfield-ai/soul/standard"),
        ("soul-v2", "v2", "higgsfield-ai/soul/v2/standard"),
        ("soul-cinema", "cinema", "higgsfield-ai/soul/cinema"),
    ],
)
def test_soul_passes_the_soul_id_as_custom_reference_id_to_the_matching_soul_model(
    higgs, project, photos, api_key, respx_mock, estimate, model, version, endpoint
):
    assert train(higgs, respx_mock, "alice", version=version).exit_code == 0
    route = respx_mock.post(f"{API}/{endpoint}").mock(return_value=submitted("req-1"))

    result = higgs(
        "image", "t2i", "--model", model, "--prompt", "Alice on a beach", "--soul", "alice", "--no-wait", "--json", "--yes"
    )

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "Alice on a beach", "custom_reference_id": SOUL_ID}


def t2i_with_soul(higgs, model: str, soul: str = "alice"):
    return higgs("image", "t2i", "--model", model, "--prompt", "Alice", "--soul", soul, "--no-wait", "--json", "--yes")


def test_a_soul_id_of_another_version_than_the_soul_model_fails_before_any_call(
    higgs, project, photos, api_key, respx_mock
):
    assert train(higgs, respx_mock, "alice", version="v1").exit_code == 0
    respx_mock.reset()

    result = t2i_with_soul(higgs, "soul-v2")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "soul_version_mismatch"
    assert error["message"] == "Soul ID alice is a v1 Soul ID; soul-v2 takes v2 Soul IDs only"
    assert "--model soul" in error["hint"]
    assert not respx_mock.calls


def test_an_unknown_soul_lists_the_registered_ones(higgs, project, photos, api_key, respx_mock):
    assert train(higgs, respx_mock, "alice").exit_code == 0
    assert train(higgs, respx_mock, "bob", soul_id=OTHER_ID).exit_code == 0
    respx_mock.reset()

    result = t2i_with_soul(higgs, "soul-v2", soul="alicia")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "soul_not_found"
    assert error["hint"] == "Registered: alice, bob."
    assert not respx_mock.calls


def test_soul_on_a_model_without_soul_ids_is_an_unsupported_flag(higgs, project, api_key, respx_mock):
    result = t2i_with_soul(higgs, "grok-image-2.0")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "unsupported_flag"
    assert error["message"] == "--soul is not supported by grok-image-2.0 t2i"
    assert not respx_mock.calls


def test_a_soul_still_training_is_checked_with_the_api_before_it_fails(higgs, project, photos, api_key, respx_mock):
    assert train(higgs, respx_mock, "alice", args=["--no-wait"]).exit_code == 0
    polls = status_route(respx_mock, "in_progress")

    result = t2i_with_soul(higgs, "soul-v2")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "soul_not_ready"
    assert "in_progress" in error["message"]
    assert polls.call_count == 1


def test_a_soul_that_finished_training_since_no_wait_is_used_right_away(
    higgs, project, photos, api_key, respx_mock, estimate, config_dir
):
    assert train(higgs, respx_mock, "alice", args=["--no-wait"]).exit_code == 0
    status_route(respx_mock, "completed")
    route = respx_mock.post(f"{API}/higgsfield-ai/soul/v2/standard").mock(return_value=submitted("req-1"))

    result = t2i_with_soul(higgs, "soul-v2")

    assert result.exit_code == 0, result.output
    assert body(route)["custom_reference_id"] == SOUL_ID
    assert souls_toml(config_dir)["souls"]["alice"]["status"] == "completed"


def test_a_failed_soul_cannot_be_used(higgs, project, photos, api_key, respx_mock):
    assert train(higgs, respx_mock, "alice", status="failed").exit_code == 1
    respx_mock.reset()

    result = t2i_with_soul(higgs, "soul-v2")

    assert json.loads(result.output)["error"]["code"] == "soul_training_failed"
    assert not respx_mock.calls


def test_soul_list_without_any_soul_ids_says_how_to_train_one(higgs):
    result = higgs("soul", "list")

    assert result.exit_code == 0
    assert "higgs soul train" in result.output
    assert json.loads(higgs("soul", "list", "--json").output)["souls"] == []


def test_soul_status_of_an_unknown_name_fails_without_calling_the_api(higgs, api_key, respx_mock):
    result = higgs("soul", "status", "alice", "--json")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "soul_not_found"
    assert "higgs soul train" in error["hint"]
    assert not respx_mock.calls


def test_soul_status_of_a_soul_id_the_api_does_not_know_says_so(higgs, project, photos, api_key, respx_mock):
    assert train(higgs, respx_mock, "alice", args=["--no-wait"]).exit_code == 0
    respx_mock.get(f"{CREATE_URL}/{SOUL_ID}").mock(return_value=httpx.Response(404, json={"detail": "Not found"}))

    result = higgs("soul", "status", "alice", "--json")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "api_soul_not_found"
    assert SOUL_ID in error["message"]
    assert "account" in error["hint"]
