"""Price, Spend and Budget: `POST /estimate/{endpoint}` and its confirmation before every submit."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

ENDPOINT = "higgsfield-ai/soul/v2/standard"
SUBMIT_URL = f"https://api.higgsfield.ai/{ENDPOINT}"
ESTIMATE_URL = f"https://api.higgsfield.ai/estimate/{ENDPOINT}"
REQUEST_ID = "d7e6c0f3-6699-4f6c-bb45-2ad7fd9158ff"

SUBMITTED = {
    "status": "queued",
    "request_id": REQUEST_ID,
    "status_url": f"https://api.higgsfield.ai/requests/{REQUEST_ID}/status",
    "cancel_url": f"https://api.higgsfield.ai/requests/{REQUEST_ID}/cancel",
}


def record(project) -> Any:
    return json.loads((project / ".hf" / "requests" / f"{REQUEST_ID}.json").read_text())


def test_the_price_is_estimated_on_the_submitted_body_and_stored_on_the_record(higgs, project, api_key, respx_mock):
    # The response from the docs ("Pricing" → Estimate a request).
    estimate = respx_mock.post(ESTIMATE_URL).mock(return_value=httpx.Response(200, json={"usd": "0.094"}))
    submit = respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(200, json=SUBMITTED))

    result = higgs(
        "gen", ENDPOINT, "-p", "prompt=Editorial portrait in soft daylight", "-p", "batch_size=2",
        "--no-validate", "--no-wait", "--json", "--yes",
    )

    assert result.exit_code == 0, result.output
    sent = estimate.calls.last.request
    assert sent.headers["Authorization"] == api_key
    assert json.loads(sent.content) == json.loads(submit.calls.last.request.content)
    assert record(project)["estimate_usd"] == "0.094"


def mock_api(respx_mock, usd: str = "0.094"):
    """An estimate of `usd` and an accepted submit; returns the submit route."""
    respx_mock.post(ESTIMATE_URL).mock(return_value=httpx.Response(200, json={"usd": usd}))
    return respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(200, json=SUBMITTED))


def gen(higgs, *args: str, input: str | None = None):
    return higgs("gen", ENDPOINT, "-p", "prompt=a cat", "--no-validate", "--no-wait", *args, input=input)


def test_json_without_yes_fails_before_submitting_and_reports_the_price(higgs, project, api_key, respx_mock):
    submit = mock_api(respx_mock)

    result = gen(higgs, "--json")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "confirmation_required"
    assert "--yes" in error["hint"]
    assert error["price_usd"] == "0.094"
    assert not submit.called
    assert not list((project / ".hf" / "requests").iterdir())


def test_the_price_is_shown_and_confirmed_before_submitting(higgs, project, api_key, respx_mock):
    submit = mock_api(respx_mock)

    result = gen(higgs, input="y\n")

    assert result.exit_code == 0, result.output
    assert "$0.094" in result.output
    assert submit.called
    assert REQUEST_ID in result.output


def test_declining_the_price_aborts_without_submitting(higgs, project, api_key, respx_mock):
    submit = mock_api(respx_mock)

    result = gen(higgs, input="n\n")

    assert result.exit_code == 1
    assert "$0.094" in result.output
    assert "Aborted" in result.output
    assert not submit.called
    assert not list((project / ".hf" / "requests").iterdir())


def test_no_input_without_yes_cannot_confirm_the_price(higgs, project, api_key, respx_mock):
    submit = mock_api(respx_mock)

    result = gen(higgs, "--no-input", input="y\n")

    assert result.exit_code == 1
    assert "--yes" in " ".join(result.output.split())
    assert not submit.called


def test_no_input_with_yes_submits(higgs, project, api_key, respx_mock):
    submit = mock_api(respx_mock)

    result = gen(higgs, "--no-input", "--yes")

    assert result.exit_code == 0, result.output
    assert submit.called


def test_the_generation_metadata_records_the_price(higgs, project, api_key, respx_mock):
    mock_api(respx_mock, usd="0.25")
    respx_mock.get(SUBMITTED["status_url"]).mock(
        return_value=httpx.Response(
            200, json={"status": "completed", "images": [{"url": "https://cdn.example.com/image-1.png"}]}
        )
    )
    respx_mock.get("https://cdn.example.com/image-1.png").mock(return_value=httpx.Response(200, content=b"png"))

    result = higgs("gen", ENDPOINT, "-p", "prompt=a cat", "--no-validate", "--json", "--yes")

    assert result.exit_code == 0, result.output
    metadata_path = json.loads(result.output)["generations"][0]["metadata_path"]
    assert json.loads(Path(metadata_path).read_text())["price_usd"] == "0.25"


def set_budget(project, budget: str) -> None:
    (project / "project.toml").write_text(f'name = "demo"\nbudget = {budget}\n\n[defaults]\n')


def add_request(project, request_id: str, status: str, estimate_usd: str | None) -> None:
    """A Request record as `higgs gen` leaves it (ADR 0002: the records are the source of truth)."""
    record = {
        "request_id": request_id,
        "endpoint": ENDPOINT,
        "params": {"prompt": "earlier"},
        "status": status,
        "submitted_at": "2026-10-29T12:00:00Z",
        "updated_at": "2026-10-29T12:01:00Z",
        "idempotency_key": f"key-{request_id}",
        "estimate_usd": estimate_usd,
    }
    (project / ".hf" / "requests" / f"{request_id}.json").write_text(json.dumps(record))


def test_going_over_the_budget_with_yes_only_warns(higgs, project, api_key, respx_mock):
    set_budget(project, "0.55")
    add_request(project, "earlier-1", "completed", "0.50")
    submit = mock_api(respx_mock, usd="0.094")

    result = gen(higgs, "--json", "--yes")

    assert result.exit_code == 0, result.output
    assert submit.called
    [warning] = json.loads(result.output)["warnings"]
    assert warning["code"] == "over_budget"
    assert warning["spend_usd"] == "0.50"
    assert warning["price_usd"] == "0.094"
    assert warning["budget_usd"] == "0.55"
    assert "Budget" in warning["message"]


def test_spend_counts_only_completed_requests(higgs, project, api_key, respx_mock):
    set_budget(project, "1.00")
    add_request(project, "completed-1", "completed", "0.50")
    add_request(project, "completed-2", "completed", "0.30")
    # Not billed (failed, nsfw, canceled) or not finished yet: none of them is Spend.
    for status in ("failed", "nsfw", "canceled", "queued", "in_progress"):
        add_request(project, f"{status}-1", status, "5.00")
    mock_api(respx_mock, usd="0.20")

    within = json.loads(gen(higgs, "--json", "--yes").output)

    assert within["warnings"] == []  # 0.80 + 0.20 is exactly the Budget

    respx_mock.post(ESTIMATE_URL).mock(return_value=httpx.Response(200, json={"usd": "0.21"}))
    [warning] = json.loads(gen(higgs, "--json", "--yes").output)["warnings"]

    assert warning["spend_usd"] == "0.80"


def test_going_over_the_budget_warns_and_asks(higgs, project, api_key, respx_mock):
    set_budget(project, "0.55")
    add_request(project, "earlier-1", "completed", "0.50")
    submit = mock_api(respx_mock)

    declined = gen(higgs, input="n\n")

    assert declined.exit_code == 1
    output = " ".join(declined.output.split())
    assert "Warning" in output and "Budget of $0.55" in output
    assert "$0.094" in output
    assert not submit.called

    accepted = gen(higgs, input="y\n")

    assert accepted.exit_code == 0, accepted.output
    assert submit.called


def test_going_over_the_budget_as_text_with_yes_warns_without_asking(higgs, project, api_key, respx_mock):
    set_budget(project, "0.55")
    add_request(project, "earlier-1", "completed", "0.50")
    submit = mock_api(respx_mock)

    result = gen(higgs, "--yes")  # no input: a prompt would abort

    assert result.exit_code == 0, result.output
    assert "Budget of $0.55" in " ".join(result.output.split())
    assert submit.called


def test_json_over_the_budget_without_yes_fails_with_the_warning(higgs, project, api_key, respx_mock):
    set_budget(project, "0.55")
    add_request(project, "earlier-1", "completed", "0.50")
    submit = mock_api(respx_mock)

    result = gen(higgs, "--json")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "confirmation_required"
    assert [warning["code"] for warning in error["warnings"]] == ["over_budget"]
    assert not submit.called


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (422, {"detail": [{"loc": ["body", "prompt"], "msg": "Field required", "type": "missing"}]}, "invalid_params"),
        (404, {"detail": "Not Found"}, "model_unavailable"),
        (401, {"detail": "Invalid credentials"}, "invalid_key"),
    ],
)
def test_a_failed_estimate_submits_nothing(higgs, project, api_key, respx_mock, status, body, code):
    respx_mock.post(ESTIMATE_URL).mock(
        return_value=httpx.Response(status, json=body, headers={"X-Correlation-ID": "corr-est"})
    )
    submit = respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(200, json=SUBMITTED))

    result = gen(higgs, "--json", "--yes")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == code
    assert error["correlation_id"] == "corr-est"
    assert not submit.called
    assert not list((project / ".hf" / "requests").iterdir())


def test_an_unknown_endpoint_is_named_as_such_when_the_estimate_fails(higgs, project, api_key, respx_mock):
    respx_mock.post(ESTIMATE_URL).mock(return_value=httpx.Response(404, json={"detail": "Not Found"}))

    error = json.loads(gen(higgs, "--json", "--yes").output)["error"]

    assert f"Endpoint {ENDPOINT} is unavailable" in error["message"]


@pytest.mark.parametrize("budget", ['"lots"', "-5", "[1, 2"])
def test_an_invalid_budget_fails_before_submitting(higgs, project, api_key, respx_mock, budget):
    set_budget(project, budget)
    submit = mock_api(respx_mock)

    result = gen(higgs, "--json", "--yes")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "invalid_project_config"
    assert "budget" in error["hint"]
    assert not submit.called


def test_a_budget_may_be_a_whole_number(higgs, project, api_key, respx_mock):
    set_budget(project, "1")
    add_request(project, "earlier-1", "completed", "0.95")
    mock_api(respx_mock)

    [warning] = json.loads(gen(higgs, "--json", "--yes").output)["warnings"]

    assert warning["budget_usd"] == "1"
