import json
import uuid

import httpx
import pytest

ENDPOINT = "higgsfield-ai/soul/v2/standard"
SUBMIT_URL = f"https://api.higgsfield.ai/{ENDPOINT}"
REQUEST_ID = "d7e6c0f3-6699-4f6c-bb45-2ad7fd9158ff"

# The submit response from the docs ("Requests and lifecycle" → Initial response).
SUBMITTED = {
    "status": "queued",
    "request_id": REQUEST_ID,
    "status_url": f"https://api.higgsfield.ai/requests/{REQUEST_ID}/status",
    "cancel_url": f"https://api.higgsfield.ai/requests/{REQUEST_ID}/cancel",
    "expires_after": 604800,
}


def submitted(**headers: str) -> httpx.Response:
    return httpx.Response(200, json=SUBMITTED, headers={"X-Correlation-ID": "corr-1", **headers})


def test_gen_submits_the_request_and_writes_its_record(higgs, project, api_key, respx_mock):
    route = respx_mock.post(SUBMIT_URL).mock(return_value=submitted())

    result = higgs(
        "gen", ENDPOINT, "-p", "prompt=Editorial portrait in soft daylight", "-p", "batch_size=2",
        "--no-validate", "--no-wait", "--json",
    )

    assert result.exit_code == 0, result.output
    sent = route.calls.last.request
    assert sent.headers["Authorization"] == api_key
    assert uuid.UUID(sent.headers["Idempotency-Key"])
    assert json.loads(sent.content) == {"prompt": "Editorial portrait in soft daylight", "batch_size": 2}

    record_path = project / ".hf" / "requests" / f"{REQUEST_ID}.json"
    record = json.loads(record_path.read_text())
    assert record["request_id"] == REQUEST_ID
    assert record["endpoint"] == ENDPOINT
    assert record["params"] == {"prompt": "Editorial portrait in soft daylight", "batch_size": 2}
    assert record["prompt"] == "Editorial portrait in soft daylight"
    assert record["status"] == "queued"
    assert record["status_url"] == SUBMITTED["status_url"]
    assert record["cancel_url"] == SUBMITTED["cancel_url"]
    assert record["idempotency_key"] == sent.headers["Idempotency-Key"]
    assert record["submitted_at"]

    output = json.loads(result.output)
    assert output["request_id"] == REQUEST_ID
    assert output["record"] == record


def gen(higgs, *args: str):
    return higgs("gen", ENDPOINT, "-p", "prompt=a cat", "--no-validate", "--no-wait", "--json", *args)


def test_gen_from_a_nested_subfolder_finds_the_project(higgs, project, api_key, respx_mock, monkeypatch):
    respx_mock.post(SUBMIT_URL).mock(return_value=submitted())
    nested = project / "generations" / "2026-10-30"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    result = gen(higgs)

    assert result.exit_code == 0, result.output
    assert (project / ".hf" / "requests" / f"{REQUEST_ID}.json").is_file()


def test_project_option_overrides_the_project_around_the_current_directory(
    higgs, project, projects_root, api_key, respx_mock
):
    respx_mock.post(SUBMIT_URL).mock(return_value=submitted())
    higgs("init", "other")

    result = gen(higgs, "--project", "other")

    assert result.exit_code == 0, result.output
    assert (projects_root / "other" / ".hf" / "requests" / f"{REQUEST_ID}.json").is_file()
    assert not (project / ".hf" / "requests" / f"{REQUEST_ID}.json").exists()


def test_project_option_with_an_unknown_name_fails(higgs, project, api_key, respx_mock):
    result = gen(higgs, "--project", "missing")

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "project_not_found"
    assert not respx_mock.calls


def test_param_values_are_sent_as_json_types(higgs, project, api_key, respx_mock):
    route = respx_mock.post(SUBMIT_URL).mock(return_value=submitted())

    result = higgs(
        "gen", ENDPOINT,
        "-p", "duration=5", "-p", "strength=0.5", "-p", "enhance=true", "-p", "seed=null",
        "-p", "images=[\"https://cdn.example.com/a.jpg\"]", "-p", "camera={\"zoom\": 1}",
        "-p", "aspect_ratio=16:9", "-p", "code=007", "-p", "prompt=a = b", "-p", "empty=",
        "-p", "label=\"42\"", "-p", "odd=NaN",
        "--no-validate", "--no-wait", "--json",
    )

    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {
        "duration": 5,
        "strength": 0.5,
        "enhance": True,
        "seed": None,
        "images": ["https://cdn.example.com/a.jpg"],
        "camera": {"zoom": 1},
        "aspect_ratio": "16:9",
        "code": "007",
        "prompt": "a = b",
        "empty": "",
        "label": "42",
        "odd": "NaN",
    }


def test_a_param_without_a_value_is_rejected(higgs, project, api_key, respx_mock):
    result = higgs("gen", ENDPOINT, "-p", "duration", "--no-validate", "--no-wait", "--json")

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "invalid_param"
    assert not respx_mock.calls


def test_gen_requires_no_validate_until_the_catalog_exists(higgs, project, api_key, respx_mock):
    # Catalog validation (#14) is not built yet.
    result = higgs("gen", ENDPOINT, "-p", "prompt=a cat", "--no-wait", "--json")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "not_implemented"
    assert "--no-validate" in error["hint"]
    assert not respx_mock.calls
    assert not list((project / ".hf" / "requests").iterdir())


# Error bodies from the docs ("Errors and retries", "Authentication", "API keys",
# "Idempotent requests", "Rate limits"); the others follow the same FastAPI envelope.
API_ERRORS = [
    (401, {"detail": "Invalid credentials"}, "invalid_key"),
    (
        403,
        {"code": "api_key_expired", "detail": "This API key has expired.", "expires_at": "2026-09-21T12:00:00Z"},
        "api_key_expired",
    ),
    (403, {"code": "api_key_disabled", "detail": "This API key is disabled."}, "api_key_disabled"),
    (403, {"detail": "Insufficient balance"}, "insufficient_funds"),
    (404, {"detail": "Not Found"}, "model_unavailable"),
    (423, {"detail": "Model is temporarily blocked"}, "model_unavailable"),
    (503, {"detail": "Model is disabled"}, "model_unavailable"),
    (
        503,
        {"detail": {"code": "admission_control_unavailable", "message": "Try again", "retryable": True}},
        "service_unavailable",
    ),
    (422, {"detail": "Idempotency-Key was already used with different request parameters"}, "idempotency_conflict"),
    (
        422,
        {"detail": [{"loc": ["body", "prompt"], "msg": "Field required", "type": "missing"}]},
        "invalid_params",
    ),
    (
        429,
        {
            "detail": {
                "code": "account_queue_full",
                "message": "Too many queued generations for this account",
                "retryable": True,
                "limit": 40,
                "retry_after_seconds": 30,
            }
        },
        "queue_full",
    ),
    (500, {"detail": "Internal Server Error"}, "api_error"),
]


@pytest.mark.parametrize(("status", "body", "code"), API_ERRORS)
def test_api_errors_map_to_specific_errors_with_the_correlation_id(
    higgs, project, api_key, respx_mock, status, body, code
):
    respx_mock.post(SUBMIT_URL).mock(
        return_value=httpx.Response(status, json=body, headers={"X-Correlation-ID": "corr-err"})
    )

    result = gen(higgs)

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == code
    assert error["status"] == status
    assert error["correlation_id"] == "corr-err"
    assert error["message"]
    assert not list((project / ".hf" / "requests").iterdir())


def test_an_api_error_as_text_shows_the_message_and_correlation_id(higgs, project, api_key, respx_mock):
    respx_mock.post(SUBMIT_URL).mock(
        return_value=httpx.Response(401, json={"detail": "Invalid credentials"}, headers={"X-Correlation-ID": "corr-err"})
    )

    result = higgs("gen", ENDPOINT, "--no-validate", "--no-wait")

    assert result.exit_code == 1
    output = " ".join(result.output.split())  # Rich wraps long lines
    assert "Invalid credentials" in output
    assert "higgs auth login" in output
    assert "corr-err" in output


def test_an_insufficient_balance_points_to_the_console(higgs, project, api_key, respx_mock):
    respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(403, json={"detail": "Insufficient balance"}))

    error = json.loads(gen(higgs).output)["error"]

    assert "console.higgsfield.ai" in error["hint"]


def test_a_submit_response_without_a_request_id_is_an_error(higgs, project, api_key, respx_mock):
    respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(200, json={"status": "queued"}))

    result = gen(higgs)

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "invalid_response"
    assert not list((project / ".hf" / "requests").iterdir())


def test_each_submit_carries_a_new_idempotency_key(higgs, project, api_key, respx_mock):
    route = respx_mock.post(SUBMIT_URL).mock(return_value=submitted())

    gen(higgs)
    gen(higgs)

    keys = {call.request.headers["Idempotency-Key"] for call in route.calls}
    assert len(keys) == 2


def test_gen_as_text_prints_the_request_id(higgs, project, api_key, respx_mock):
    respx_mock.post(SUBMIT_URL).mock(return_value=submitted())

    result = higgs("gen", ENDPOINT, "-p", "prompt=a cat", "--no-validate", "--no-wait")

    assert result.exit_code == 0
    assert REQUEST_ID in result.output


def test_a_network_failure_is_reported_as_an_error(higgs, project, api_key, respx_mock):
    respx_mock.post(SUBMIT_URL).mock(side_effect=httpx.ConnectError("connection refused"))

    result = gen(higgs)

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "network_error"


def test_gen_outside_a_project_fails_with_a_hint(higgs, api_key, respx_mock, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    result = gen(higgs)

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "not_in_project"
    assert "higgs init" in error["hint"] and "--project" in error["hint"]
    assert not respx_mock.calls
