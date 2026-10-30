"""`higgs status`, `higgs list`, `higgs sync` and `higgs download`: Requests after the submit."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

ENDPOINT = "higgsfield-ai/soul/v2/standard"
REQUEST_ID = "d7e6c0f3-6699-4f6c-bb45-2ad7fd9158ff"
OTHER_ID = "5a1b2c3d-0000-4f6c-bb45-2ad7fd9158aa"

DAY = "generations/2026-10-30"


def status_url(request_id: str) -> str:
    return f"https://api.higgsfield.ai/requests/{request_id}/status"


def submitted(request_id: str) -> dict[str, Any]:
    """The submit response from the docs ("Requests and lifecycle" → Initial response)."""
    return {
        "status": "queued",
        "request_id": request_id,
        "status_url": status_url(request_id),
        "cancel_url": f"https://api.higgsfield.ai/requests/{request_id}/cancel",
        "expires_after": 604800,
    }


# Status bodies from the docs ("Requests and lifecycle").
def in_progress(request_id: str = REQUEST_ID) -> dict[str, Any]:
    return {"status": "in_progress", "request_id": request_id}


def completed(request_id: str = REQUEST_ID, url: str = "https://cdn.example.com/image-1.jpg") -> dict[str, Any]:
    return {
        "status": "completed",
        "request_id": request_id,
        "expires_at": "2026-10-31T12:05:12.481Z",
        "images": [{"url": url}],
    }


def submit(
    higgs, respx_mock, request_id: str = REQUEST_ID, *, endpoint: str = ENDPOINT, prompt: str = "a cat"
) -> None:
    """Submit a Request with `--no-wait`, so its record exists in the Project; then forget the calls."""
    respx_mock.post(f"https://api.higgsfield.ai/{endpoint}").mock(
        return_value=httpx.Response(200, json=submitted(request_id))
    )
    result = higgs("gen", endpoint, "-p", f"prompt={prompt}", "--no-validate", "--no-wait", "--json")
    assert result.exit_code == 0, result.output
    respx_mock.reset()


def record_of(project: Path, request_id: str = REQUEST_ID) -> Any:
    return json.loads((project / ".hf" / "requests" / f"{request_id}.json").read_text())


def test_status_of_a_request_refreshes_it_from_the_api(higgs, project, api_key, respx_mock):
    submit(higgs, respx_mock)
    route = respx_mock.get(status_url(REQUEST_ID)).mock(return_value=httpx.Response(200, json=in_progress()))

    result = higgs("status", REQUEST_ID, "--json")

    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert route.calls.last.request.headers["Authorization"] == api_key
    record = record_of(project)
    assert record["status"] == "in_progress"
    output = json.loads(result.output)
    assert output["request_id"] == REQUEST_ID
    assert output["status"] == "in_progress"
    assert output["record"] == record


def test_status_without_an_id_refreshes_and_shows_the_pending_requests(higgs, project, api_key, respx_mock):
    submit(higgs, respx_mock, OTHER_ID)
    finished = respx_mock.get(status_url(OTHER_ID)).mock(return_value=httpx.Response(200, json=completed(OTHER_ID)))
    assert higgs("status", OTHER_ID, "--json").exit_code == 0
    submit(higgs, respx_mock, REQUEST_ID)
    pending = respx_mock.get(status_url(REQUEST_ID)).mock(return_value=httpx.Response(200, json=in_progress()))

    result = higgs("status", "--json")

    assert result.exit_code == 0, result.output
    assert finished.call_count == 0  # a terminal Request is not asked about again
    assert pending.call_count == 1
    output = json.loads(result.output)
    assert [(item["request_id"], item["status"]) for item in output["requests"]] == [(REQUEST_ID, "in_progress")]
    assert record_of(project)["status"] == "in_progress"


def test_status_of_an_unknown_request_fails_without_calling_the_api(higgs, project, api_key, respx_mock):
    result = higgs("status", REQUEST_ID, "--json")

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "request_not_found"
    assert not respx_mock.calls


def test_list_shows_the_project_history_from_local_files_only(higgs, project, api_key, respx_mock, clock):
    submit(higgs, respx_mock, REQUEST_ID, prompt="Sunset beach")
    clock.advance(minutes=5)
    submit(higgs, respx_mock, OTHER_ID, endpoint="kling-video/v3.0/pro/image-to-video", prompt="Waves")

    result = higgs("list", "--json")

    assert result.exit_code == 0, result.output
    assert not respx_mock.calls
    listed = json.loads(result.output)["requests"]
    assert [item["request_id"] for item in listed] == [REQUEST_ID, OTHER_ID]
    assert listed[1]["naming"] == {"model_slug": "kling-3-0-pro", "mode": "i2v", "slug": "waves", "version": 1}
    assert listed[0] == record_of(project, REQUEST_ID)


THIRD_ID = "9f8e7d6c-1111-4f6c-bb45-2ad7fd9158bb"


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (["--status", "completed"], [REQUEST_ID]),
        (["--status", "queued", "--status", "completed"], [REQUEST_ID, OTHER_ID, THIRD_ID]),
        (["--status", "pending"], [OTHER_ID, THIRD_ID]),
        (["--model", "kling-video"], [OTHER_ID, THIRD_ID]),
        (["--model", "soul"], [REQUEST_ID]),
        (["--mode", "t2v"], [THIRD_ID]),
        (["--model", "kling-video", "--mode", "i2v"], [OTHER_ID]),
        (["--since", "2026-10-31"], [OTHER_ID, THIRD_ID]),
        (["--date", "2026-10-31"], [OTHER_ID]),
        (["--status", "failed"], []),
    ],
)
def test_list_filters_by_status_model_mode_and_date(higgs, project, api_key, respx_mock, clock, filters, expected):
    submit(higgs, respx_mock, REQUEST_ID)
    respx_mock.get(status_url(REQUEST_ID)).mock(return_value=httpx.Response(200, json=completed()))
    assert higgs("status", REQUEST_ID, "--json").exit_code == 0
    clock.advance(days=1)
    submit(higgs, respx_mock, OTHER_ID, endpoint="kling-video/v3.0/pro/image-to-video")
    clock.advance(days=1)
    submit(higgs, respx_mock, THIRD_ID, endpoint="kling-video/v3.0/pro/text-to-video")
    respx_mock.reset()

    result = higgs("list", *filters, "--json")

    assert result.exit_code == 0, result.output
    assert not respx_mock.calls
    assert [item["request_id"] for item in json.loads(result.output)["requests"]] == expected


def test_sync_downloads_completed_requests_and_reports_the_running_ones(higgs, project, api_key, respx_mock):
    submit(higgs, respx_mock, REQUEST_ID, prompt="Sunset beach")
    submit(higgs, respx_mock, OTHER_ID, prompt="Waves")
    respx_mock.get(status_url(REQUEST_ID)).mock(return_value=httpx.Response(200, json=completed()))
    respx_mock.get(status_url(OTHER_ID)).mock(return_value=httpx.Response(200, json=in_progress(OTHER_ID)))
    respx_mock.get("https://cdn.example.com/image-1.jpg").mock(return_value=httpx.Response(200, content=b"jpeg-1"))

    result = higgs("sync", "--json")

    assert result.exit_code == 0, result.output
    name = "20261030-1200_soul-v2_t2i_sunset-beach_v01.jpg"
    assert (project / DAY / name).read_bytes() == b"jpeg-1"
    assert json.loads((project / DAY / f"{name}.json").read_text())["request_id"] == REQUEST_ID
    record = record_of(project, REQUEST_ID)
    assert record["status"] == "completed"
    assert record["downloaded_at"] is not None
    assert record["files"] == [f"{DAY}/{name}"]
    assert record_of(project, OTHER_ID)["status"] == "in_progress"
    synced = {item["request_id"]: item for item in json.loads(result.output)["requests"]}
    assert synced[REQUEST_ID]["outcome"] == "downloaded"
    assert synced[REQUEST_ID]["files"] == [f"{DAY}/{name}"]
    assert synced[OTHER_ID]["outcome"] == "pending"
    assert synced[OTHER_ID]["status"] == "in_progress"
    assert len(respx_mock.calls) == 3  # two status checks and one download, no waiting


def test_sync_downloads_each_completed_request_exactly_once(higgs, project, api_key, respx_mock):
    submit(higgs, respx_mock, REQUEST_ID)
    respx_mock.get(status_url(REQUEST_ID)).mock(return_value=httpx.Response(200, json=completed()))
    respx_mock.get("https://cdn.example.com/image-1.jpg").mock(return_value=httpx.Response(200, content=b"jpeg-1"))
    assert higgs("sync", "--json").exit_code == 0
    respx_mock.reset()

    result = higgs("sync", "--json")

    assert result.exit_code == 0, result.output
    assert not respx_mock.calls
    assert json.loads(result.output)["requests"] == []


# Terminal failures from the docs ("Errors and retries", "Webhooks"); canceled has the same shape.
@pytest.mark.parametrize(
    "body",
    [
        {"status": "failed", "request_id": REQUEST_ID, "error": "Generation failed"},
        {"status": "nsfw", "request_id": REQUEST_ID, "error": None},
        {"status": "canceled", "request_id": REQUEST_ID},
    ],
)
def test_sync_records_an_unsuccessful_request_and_does_not_ask_again(higgs, project, api_key, respx_mock, body):
    submit(higgs, respx_mock, REQUEST_ID)
    route = respx_mock.get(status_url(REQUEST_ID)).mock(return_value=httpx.Response(200, json=body))

    result = higgs("sync", "--json")

    assert result.exit_code == 0, result.output
    [synced] = json.loads(result.output)["requests"]
    assert (synced["outcome"], synced["status"]) == ("unsuccessful", body["status"])
    assert synced["error"] == body.get("error")
    record = record_of(project)
    assert record["status"] == body["status"]
    assert record["downloaded_at"] is None
    assert len(respx_mock.calls) == 1  # nothing to download

    again = higgs("sync", "--json")

    assert json.loads(again.output)["requests"] == []
    assert route.call_count == 1


def test_sync_reports_a_completed_request_whose_links_expired_without_calling_the_api(
    higgs, project, api_key, respx_mock, clock
):
    submit(higgs, respx_mock, REQUEST_ID)
    respx_mock.get(status_url(REQUEST_ID)).mock(return_value=httpx.Response(200, json=completed()))
    assert higgs("status", REQUEST_ID, "--json").exit_code == 0  # completed, not downloaded
    clock.advance(days=2)  # past `expires_at` (2026-10-31 12:05)
    respx_mock.reset()

    result = higgs("sync", "--json")

    assert result.exit_code == 0, result.output
    assert not respx_mock.calls
    [synced] = json.loads(result.output)["requests"]
    assert (synced["outcome"], synced["status"]) == ("expired", "completed")
    assert record_of(project)["downloaded_at"] is None


def test_sync_goes_on_after_a_request_fails_to_download_and_exits_with_an_error(
    higgs, project, api_key, respx_mock
):
    submit(higgs, respx_mock, REQUEST_ID)
    submit(higgs, respx_mock, OTHER_ID)
    respx_mock.get(status_url(REQUEST_ID)).mock(return_value=httpx.Response(200, json=completed()))
    respx_mock.get(status_url(OTHER_ID)).mock(
        return_value=httpx.Response(200, json=completed(OTHER_ID, "https://cdn.example.com/image-2.jpg"))
    )
    respx_mock.get("https://cdn.example.com/image-1.jpg").mock(return_value=httpx.Response(403))
    respx_mock.get("https://cdn.example.com/image-2.jpg").mock(return_value=httpx.Response(200, content=b"jpeg-2"))

    result = higgs("sync", "--json")

    assert result.exit_code == 1
    output = json.loads(result.output)
    assert output["error"]["code"] == "sync_incomplete"
    synced = {item["request_id"]: item for item in output["requests"]}
    assert synced[REQUEST_ID]["outcome"] == "error"
    assert synced[REQUEST_ID]["failure"]["code"] == "download_failed"
    assert synced[OTHER_ID]["outcome"] == "downloaded"
    assert record_of(project, REQUEST_ID)["downloaded_at"] is None  # tried again by the next sync
    assert record_of(project, OTHER_ID)["downloaded_at"] is not None


NAME = "20261030-1200_soul-v2_t2i_a-cat_v01.jpg"


def synced_request(higgs, respx_mock) -> None:
    """A `--no-wait` Request, completed and downloaded by `sync`."""
    submit(higgs, respx_mock, REQUEST_ID)
    respx_mock.get(status_url(REQUEST_ID)).mock(return_value=httpx.Response(200, json=completed()))
    respx_mock.get("https://cdn.example.com/image-1.jpg").mock(return_value=httpx.Response(200, content=b"jpeg-1"))
    assert higgs("sync", "--json").exit_code == 0
    respx_mock.reset()


def test_download_fetches_a_completed_requests_generations_again(higgs, project, api_key, respx_mock, clock):
    synced_request(higgs, respx_mock)
    (project / DAY / NAME).unlink()
    clock.advance(hours=1)

    result = higgs("download", REQUEST_ID, "--json")

    assert result.exit_code == 0, result.output
    assert (project / DAY / NAME).read_bytes() == b"jpeg-1"
    assert len(respx_mock.calls) == 2  # fresh links from the status, then the file
    record = record_of(project)
    assert record["downloaded_at"] == "2026-10-30T13:00:00Z"
    assert record["files"] == [f"{DAY}/{NAME}"]
    output = json.loads(result.output)
    assert output["request_id"] == REQUEST_ID
    assert [item["path"] for item in output["generations"]] == [str(project / DAY / NAME)]


def test_download_after_the_links_expired_fails_without_calling_the_api(higgs, project, api_key, respx_mock, clock):
    synced_request(higgs, respx_mock)
    clock.advance(days=2)  # past `expires_at` (2026-10-31 12:05)

    result = higgs("download", REQUEST_ID, "--json")

    assert result.exit_code == 1
    assert not respx_mock.calls
    error = json.loads(result.output)["error"]
    assert error["code"] == "request_expired"
    assert "2026-10-31" in error["message"]
    assert (project / DAY / NAME).read_bytes() == b"jpeg-1"  # what was downloaded stays


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (in_progress(), "request_pending"),
        ({"status": "queued", "request_id": REQUEST_ID}, "request_pending"),
        ({"status": "failed", "request_id": REQUEST_ID, "error": "Generation failed"}, "request_failed"),
    ],
)
def test_download_of_a_request_that_has_not_completed_is_a_clear_error(higgs, project, api_key, respx_mock, body, code):
    submit(higgs, respx_mock, REQUEST_ID)
    respx_mock.get(status_url(REQUEST_ID)).mock(return_value=httpx.Response(200, json=body))

    result = higgs("download", REQUEST_ID, "--json")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == code
    assert REQUEST_ID in error["message"]
    assert len(respx_mock.calls) == 1  # the status check, no download
    assert record_of(project)["status"] == body["status"]


def test_download_of_a_request_known_to_have_failed_does_not_call_the_api(higgs, project, api_key, respx_mock):
    submit(higgs, respx_mock, REQUEST_ID)
    respx_mock.get(status_url(REQUEST_ID)).mock(
        return_value=httpx.Response(200, json={"status": "nsfw", "request_id": REQUEST_ID, "error": None})
    )
    assert higgs("status", REQUEST_ID, "--json").exit_code == 0
    respx_mock.reset()

    result = higgs("download", REQUEST_ID, "--json")

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "request_nsfw"
    assert not respx_mock.calls


def test_download_of_a_request_deleted_on_the_api_side_does_not_call_the_api(higgs, project, api_key, respx_mock):
    synced_request(higgs, respx_mock)
    respx_mock.delete(f"https://api.higgsfield.ai/requests/{REQUEST_ID}").mock(
        return_value=httpx.Response(200, json={"request_id": REQUEST_ID, "status": "deleted"})
    )
    assert higgs("delete", REQUEST_ID, "--json").exit_code == 0
    respx_mock.reset()

    result = higgs("download", REQUEST_ID, "--json")

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "request_deleted"
    assert not respx_mock.calls


def test_list_as_text_is_a_table_of_the_requests(higgs, project, api_key, respx_mock, clock, monkeypatch):
    monkeypatch.setenv("COLUMNS", "160")  # a terminal wide enough for every column
    synced_request(higgs, respx_mock)
    respx_mock.delete(f"https://api.higgsfield.ai/requests/{REQUEST_ID}").mock(
        return_value=httpx.Response(200, json={"request_id": REQUEST_ID, "status": "deleted"})
    )
    assert higgs("delete", REQUEST_ID, "--json").exit_code == 0
    # The exact price is recorded by the estimate step of `gen`; written here by hand.
    path = project / ".hf" / "requests" / f"{REQUEST_ID}.json"
    path.write_text(json.dumps({**record_of(project), "estimate_usd": "0.35"}))
    clock.advance(hours=2)
    submit(higgs, respx_mock, OTHER_ID, endpoint="kling-video/v3.0/pro/image-to-video", prompt="Waves")

    result = higgs("list")

    assert result.exit_code == 0, result.output
    assert not respx_mock.calls
    lines = result.output.splitlines()
    [first] = [line for line in lines if "d7e6c0f3" in line]
    for cell in ("2026-10-30 12:00", "t2i", "a-cat v01", "completed", "deleted", "$0.35", "1"):
        assert cell in first, first
    [second] = [line for line in lines if "5a1b2c3d" in line]
    for cell in ("2026-10-30 14:00", "kling-3-0-pro", "i2v", "waves v01", "queued"):
        assert cell in second, second


def test_list_without_requests_says_so(higgs, project):
    result = higgs("list")

    assert result.exit_code == 0, result.output
    assert "No Requests" in result.output


def test_sync_as_text_lists_what_it_downloaded_and_what_still_runs(higgs, project, api_key, respx_mock):
    submit(higgs, respx_mock, REQUEST_ID)
    submit(higgs, respx_mock, OTHER_ID)
    respx_mock.get(status_url(REQUEST_ID)).mock(return_value=httpx.Response(200, json=completed()))
    respx_mock.get(status_url(OTHER_ID)).mock(return_value=httpx.Response(200, json=in_progress(OTHER_ID)))
    respx_mock.get("https://cdn.example.com/image-1.jpg").mock(return_value=httpx.Response(200, content=b"jpeg-1"))

    result = higgs("sync")

    assert result.exit_code == 0, result.output
    assert f"Downloaded Request {REQUEST_ID}" in result.output
    assert f"{DAY}/{NAME}" in result.output
    assert f"Request {OTHER_ID} is still in_progress" in result.output


def test_status_and_sync_as_text_say_when_there_is_nothing_to_do(higgs, project, api_key, respx_mock):
    assert "No pending Requests" in higgs("status").output
    assert "Nothing to sync" in higgs("sync").output
    assert not respx_mock.calls
