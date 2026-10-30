import json
from pathlib import Path
from typing import Any

import httpx
import pytest

ENDPOINT = "higgsfield-ai/soul/v2/standard"
REQUEST_ID = "d7e6c0f3-6699-4f6c-bb45-2ad7fd9158ff"
CANCEL_URL = f"https://api.higgsfield.ai/requests/{REQUEST_ID}/cancel"
DELETE_URL = f"https://api.higgsfield.ai/requests/{REQUEST_ID}"

# The submit response from the docs ("Requests and lifecycle" → Initial response).
SUBMITTED = {
    "status": "queued",
    "request_id": REQUEST_ID,
    "status_url": f"https://api.higgsfield.ai/requests/{REQUEST_ID}/status",
    "cancel_url": CANCEL_URL,
    "expires_after": 604800,
}


def submit(higgs, respx_mock, *args: str) -> None:
    """Submit a Request through the CLI, so its record exists in the Project."""
    respx_mock.post(f"https://api.higgsfield.ai/{ENDPOINT}").mock(return_value=httpx.Response(200, json=SUBMITTED))
    result = higgs("gen", ENDPOINT, "-p", "prompt=a cat", "--no-validate", "--no-wait", "--json", *args)
    assert result.exit_code == 0, result.output


def record_of(project: Path) -> Any:
    return json.loads((project / ".hf" / "requests" / f"{REQUEST_ID}.json").read_text())


def test_cancel_of_a_queued_request_marks_it_canceled(higgs, project, api_key, respx_mock):
    submit(higgs, respx_mock)
    # The docs: "A successful cancellation returns 202 Accepted with an empty body."
    route = respx_mock.post(CANCEL_URL).mock(return_value=httpx.Response(202))

    result = higgs("cancel", REQUEST_ID, "--json")

    assert result.exit_code == 0, result.output
    assert route.calls.last.request.headers["Authorization"] == api_key
    record = record_of(project)
    assert record["status"] == "canceled"
    output = json.loads(result.output)
    assert output["request_id"] == REQUEST_ID
    assert output["status"] == "canceled"
    assert output["record"] == record


def test_cancel_of_an_in_progress_request_reports_it_cannot_be_canceled(higgs, project, api_key, respx_mock):
    submit(higgs, respx_mock)
    # The docs: "If processing has started, the API returns 400 Bad Request." (FastAPI envelope)
    respx_mock.post(CANCEL_URL).mock(
        return_value=httpx.Response(
            400, json={"detail": "Request is already in progress"}, headers={"X-Correlation-ID": "corr-cancel"}
        )
    )

    result = higgs("cancel", REQUEST_ID, "--json")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "cannot_cancel"
    assert "in progress" in error["message"]
    assert error["status"] == 400
    assert error["correlation_id"] == "corr-cancel"
    assert record_of(project)["status"] == "queued"


def test_delete_records_the_deletion_and_leaves_local_files_in_place(higgs, project, api_key, respx_mock):
    submit(higgs, respx_mock)
    generation = project / "generations" / "2026-10-30" / "20261030-1200_soul_t2i_a-cat_v01.png"
    generation.parent.mkdir(parents=True)
    generation.write_bytes(b"png")
    generation.with_name(generation.name + ".json").write_text("{}")
    files_before = sorted(p for p in project.rglob("*") if p.is_file())
    # The docs ("Data retention and deletion"): 200 with the request id and `deleted`.
    route = respx_mock.delete(DELETE_URL).mock(
        return_value=httpx.Response(200, json={"request_id": REQUEST_ID, "status": "deleted"})
    )

    result = higgs("delete", REQUEST_ID, "--json")

    assert result.exit_code == 0, result.output
    assert route.calls.last.request.headers["Authorization"] == api_key
    assert sorted(p for p in project.rglob("*") if p.is_file()) == files_before
    assert generation.read_bytes() == b"png"
    record = record_of(project)
    assert record["deleted_at"]
    assert record["status"] == "queued"  # the Request's own outcome is unchanged
    output = json.loads(result.output)
    assert output["request_id"] == REQUEST_ID
    assert output["deleted_at"] == record["deleted_at"]
    assert output["record"] == record


def test_delete_of_an_active_request_reports_the_conflict(higgs, project, api_key, respx_mock):
    submit(higgs, respx_mock)
    # The docs: "A request that is still queued or in_progress returns 409 Conflict." (FastAPI envelope)
    respx_mock.delete(DELETE_URL).mock(
        return_value=httpx.Response(
            409, json={"detail": "Request is still running"}, headers={"X-Correlation-ID": "corr-delete"}
        )
    )

    result = higgs("delete", REQUEST_ID, "--json")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "request_active"
    assert "queued or in progress" in error["message"]
    assert "higgs cancel" in error["hint"]
    assert error["status"] == 409
    assert error["correlation_id"] == "corr-delete"
    assert record_of(project)["deleted_at"] is None


@pytest.mark.parametrize(("command", "method", "url"), [("cancel", "POST", CANCEL_URL), ("delete", "DELETE", DELETE_URL)])
def test_a_request_unknown_to_the_api_is_reported_as_such(higgs, project, api_key, respx_mock, command, method, url):
    submit(higgs, respx_mock)
    # The docs: 404 means "The request does not exist or belongs to another account."
    respx_mock.route(method=method, url=url).mock(return_value=httpx.Response(404, json={"detail": "Not Found"}))

    result = higgs(command, REQUEST_ID, "--json")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "api_request_not_found"
    assert REQUEST_ID in error["message"]
    assert error["status"] == 404


@pytest.mark.parametrize("command", ["cancel", "delete"])
def test_a_request_id_without_a_local_record_fails_without_calling_the_api(
    higgs, project, api_key, respx_mock, command
):
    # The Project is the scope: an id it has no record of is never sent to the API.
    result = higgs(command, REQUEST_ID, "--json")

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "request_not_found"
    assert not respx_mock.calls


def test_project_option_cancels_a_request_of_another_project(higgs, project, projects_root, api_key, respx_mock):
    higgs("init", "other")
    submit(higgs, respx_mock, "--project", "other")
    respx_mock.post(CANCEL_URL).mock(return_value=httpx.Response(202))

    result = higgs("cancel", REQUEST_ID, "--project", "other", "--json")

    assert result.exit_code == 0, result.output
    assert record_of(projects_root / "other")["status"] == "canceled"


@pytest.mark.parametrize(
    ("command", "response", "expected"),
    [
        ("cancel", httpx.Response(202), "Canceled"),
        ("delete", httpx.Response(200, json={"request_id": REQUEST_ID, "status": "deleted"}), "local files are kept"),
    ],
)
def test_cancel_and_delete_as_text_name_the_request(higgs, project, api_key, respx_mock, command, response, expected):
    submit(higgs, respx_mock)
    respx_mock.route(url__startswith=DELETE_URL).mock(return_value=response)

    result = higgs(command, REQUEST_ID)

    assert result.exit_code == 0, result.output
    assert REQUEST_ID in result.output
    assert expected in result.output
