"""`higgs usage`: the Project's Spend from its Request records, and the account summary from `GET /models/usage`."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

SOUL = "higgsfield-ai/soul/v2/standard"


def add_request(
    project: Path,
    request_id: str,
    status: str,
    estimate_usd: str | None,
    *,
    endpoint: str = SOUL,
    submitted_at: str = "2026-10-29T12:00:00Z",
) -> None:
    """A Request record as `higgs gen` leaves it (ADR 0002: the records are the source of truth)."""
    record = {
        "request_id": request_id,
        "endpoint": endpoint,
        "params": {"prompt": "earlier"},
        "status": status,
        "submitted_at": submitted_at,
        "updated_at": submitted_at,
        "idempotency_key": f"key-{request_id}",
        "estimate_usd": estimate_usd,
    }
    (project / ".hf" / "requests" / f"{request_id}.json").write_text(json.dumps(record))


def set_budget(project: Path, budget: str) -> None:
    (project / "project.toml").write_text(f'name = "demo"\nbudget = {budget}\n\n[defaults]\n')


def usage(higgs, *args: str) -> Any:
    result = higgs("usage", "--json", *args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_usage_shows_the_project_spend_of_completed_requests_and_what_is_left_of_the_budget(higgs, project):
    set_budget(project, "2.5")
    add_request(project, "completed-1", "completed", "0.50")
    add_request(project, "completed-2", "completed", "0.30")
    # Not billed (failed, nsfw, canceled) or not finished yet: none of them is Spend.
    for status in ("failed", "nsfw", "canceled", "queued", "in_progress"):
        add_request(project, f"{status}-1", status, "5.00")

    output = usage(higgs)

    local = output["project"]
    assert local["name"] == "demo"
    assert local["spend_usd"] == "0.80"
    assert local["requests"] == 2
    assert local["budget_usd"] == "2.5"
    assert local["remaining_usd"] == "1.70"


def test_usage_breaks_the_spend_down_by_model_and_mode(higgs, project):
    # Tiers of one Model add up to that Model, as `higgs models list` shows it.
    add_request(project, "kling-pro", "completed", "0.50", endpoint="kling-video/v3.0/pro/image-to-video")
    add_request(project, "kling-std", "completed", "0.25", endpoint="kling-video/v3.0/std/image-to-video")
    add_request(project, "kling-t2v", "completed", "0.10", endpoint="kling-video/v3.0/pro/text-to-video")
    add_request(project, "kling-failed", "failed", "9.00", endpoint="kling-video/v3.0/pro/image-to-video")
    add_request(project, "soul", "completed", "0.094")
    # An Endpoint the catalog lacks (sent with --no-validate) is named after its id.
    add_request(project, "raw", "completed", "0.05", endpoint="acme/foo/v1/text-to-video")

    by_model = usage(higgs)["project"]["by_model"]

    assert by_model == [
        {"model": "kling-3.0", "mode": "i2v", "requests": 2, "spend_usd": "0.75"},
        {"model": "kling-3.0", "mode": "t2v", "requests": 1, "spend_usd": "0.10"},
        {"model": "soul-v2", "mode": "t2i", "requests": 1, "spend_usd": "0.094"},
        {"model": "acme-foo-v1", "mode": "t2v", "requests": 1, "spend_usd": "0.05"},
    ]


USAGE_URL = "https://api.higgsfield.ai/models/usage"

# The response from the docs ("Usage and billing" → Usage analytics).
DOCS_USAGE: dict[str, Any] = {
    "time_series": [
        {
            "bucket": "2026-10-23T00:00:00Z",
            "results": [
                {
                    "model": "higgsfield-ai/soul/v2/standard",
                    "unit": "generation",
                    "quantity": "120",
                    "unit_price": "0.0940",
                    "cost": "11.2800",
                    "currency": "USD",
                    "auth_method": "3f0c8a2e-5b7d-4e19-9a61-2c4d8e7f1a90",
                }
            ],
        }
    ],
    "total": 23,
    "page": 1,
    "limit": 50,
}


def test_usage_shows_the_account_summary_of_the_last_30_days(higgs, project, api_key, respx_mock):
    later = {
        "bucket": "2026-10-24T00:00:00Z",
        "results": [
            {"model": "higgsfield-ai/soul/v2/standard", "unit": "generation", "quantity": "10",
             "unit_price": "0.0940", "cost": "0.9400", "currency": "USD"},
            {"model": "kling-video/v3.0/pro/image-to-video", "unit": "second", "quantity": "50",
             "unit_price": "0.4000", "cost": "20.0000", "currency": "USD"},
        ],
    }
    route = respx_mock.get(USAGE_URL).mock(
        return_value=httpx.Response(200, json={**DOCS_USAGE, "time_series": [*DOCS_USAGE["time_series"], later]})
    )

    account = usage(higgs)["account"]

    sent = route.calls.last.request
    assert sent.headers["Authorization"] == api_key
    # Today is 2026-10-30 12:00 UTC (the fake clock): the 30 UTC days up to now, by day.
    assert sent.url.params["start"] == "2026-10-01T00:00:00Z"
    assert sent.url.params["end"] == "2026-10-30T12:00:00Z"
    assert sent.url.params["timeframe"] == "day"
    assert account["start"] == "2026-10-01T00:00:00Z"
    assert account["end"] == "2026-10-30T12:00:00Z"
    assert account["cost_usd"] == "32.2200"
    assert account["by_model"] == [
        {"model": "kling-video/v3.0/pro/image-to-video", "unit": "second", "quantity": "50",
         "cost": "20.0000", "currency": "USD"},
        {"model": "higgsfield-ai/soul/v2/standard", "unit": "generation", "quantity": "130",
         "cost": "12.2200", "currency": "USD"},
    ]
    assert account["raw"] is None


def test_an_account_summary_in_an_unknown_shape_is_kept_as_it_came(higgs, project, api_key, respx_mock):
    body = {"usage": [{"model_id": "soul", "spent": 3}]}
    respx_mock.get(USAGE_URL).mock(return_value=httpx.Response(200, json=body))

    output = usage(higgs)

    assert output["account"]["raw"] == body
    assert output["account"]["by_model"] == []
    assert output["account"]["cost_usd"] is None  # unknown, not zero
    assert output["warnings"] == []


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(401, json={"detail": "Invalid API key"}), "invalid_key"),
        (httpx.Response(500, json={"detail": "Internal error"}), "api_error"),
        (httpx.ConnectError("connection refused"), "network_error"),
        (httpx.Response(200, text="<html>"), "invalid_response"),
    ],
)
def test_a_failing_account_call_still_shows_the_project_spend(higgs, project, api_key, respx_mock, response, code):
    add_request(project, "completed-1", "completed", "0.50")
    respx_mock.get(USAGE_URL).mock(side_effect=[response])  # one call: no retries

    output = usage(higgs)  # exit code 0: the Project part is all there

    assert output["project"]["spend_usd"] == "0.50"
    assert output["account"] is None
    [warning] = output["warnings"]
    assert warning["code"] == "account_usage_unavailable"
    assert warning["error"]["code"] == code
    assert warning["message"]


def test_without_a_key_usage_shows_the_project_spend_and_says_why_the_account_is_missing(higgs, project, respx_mock):
    add_request(project, "completed-1", "completed", "0.50")

    output = usage(higgs)

    assert not respx_mock.calls
    assert output["project"]["spend_usd"] == "0.50"
    assert output["account"] is None
    [warning] = output["warnings"]
    assert warning["error"]["code"] == "missing_key"
    assert "higgs auth login" in warning["error"]["hint"]


def test_outside_a_project_usage_shows_only_the_account(higgs, projects_root, api_key, respx_mock, monkeypatch):
    monkeypatch.chdir(projects_root)
    respx_mock.get(USAGE_URL).mock(return_value=httpx.Response(200, json=DOCS_USAGE))

    output = usage(higgs)

    assert output["project"] is None
    assert output["account"]["cost_usd"] == "11.2800"
    assert output["warnings"] == []


def test_outside_a_project_a_failing_account_call_is_an_error(higgs, projects_root, api_key, respx_mock, monkeypatch):
    monkeypatch.chdir(projects_root)
    respx_mock.get(USAGE_URL).mock(return_value=httpx.Response(401, json={"detail": "Invalid API key"}))

    result = higgs("usage", "--json")

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "invalid_key"


def test_usage_of_another_project_by_name(higgs, project, projects_root, respx_mock, monkeypatch):
    add_request(project, "completed-1", "completed", "0.50")
    monkeypatch.chdir(projects_root)

    output = usage(higgs, "--project", "demo")

    assert output["project"]["name"] == "demo"
    assert output["project"]["spend_usd"] == "0.50"

    result = higgs("usage", "--project", "missing", "--json")

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "project_not_found"


def line_with(output: str, *cells: str) -> str:
    """The one line of `output` containing every cell."""
    [line] = [line for line in output.splitlines() if all(cell in line for cell in cells)]
    return line


def test_usage_as_text_shows_the_spend_the_account_and_where_the_balance_is(
    higgs, project, api_key, respx_mock, monkeypatch
):
    monkeypatch.setenv("COLUMNS", "160")  # a terminal wide enough for every column
    set_budget(project, "2.5")
    add_request(project, "kling-pro", "completed", "0.50", endpoint="kling-video/v3.0/pro/image-to-video")
    add_request(project, "soul", "completed", "0.30")
    respx_mock.get(USAGE_URL).mock(return_value=httpx.Response(200, json=DOCS_USAGE))

    result = higgs("usage")

    assert result.exit_code == 0, result.output
    output = result.stdout
    line_with(output, "Spend", "$0.80", "2 completed Requests")
    line_with(output, "Budget", "$2.5", "$1.70 left")
    line_with(output, "kling-3.0", "i2v", "1", "$0.50")
    line_with(output, "soul-v2", "t2i", "1", "$0.30")
    line_with(output, "2026-10-01", "2026-10-30")
    line_with(output, "higgsfield-ai/soul/v2/standard", "120", "generation", "$11.2800")
    line_with(output, "Total", "$11.2800")
    line_with(output, "balance", "console.higgsfield.ai")


def test_usage_as_text_over_the_budget_says_by_how_much(higgs, project, respx_mock):
    set_budget(project, "0.5")
    add_request(project, "soul", "completed", "0.80")

    result = higgs("usage")

    assert result.exit_code == 0, result.output
    line_with(result.stdout, "Budget", "$0.5", "$0.30 over")


def test_usage_as_text_warns_when_the_account_is_unavailable_and_still_shows_the_spend(
    higgs, project, api_key, respx_mock
):
    add_request(project, "soul", "completed", "0.30")
    respx_mock.get(USAGE_URL).mock(return_value=httpx.Response(401, json={"detail": "Invalid API key"}))

    result = higgs("usage")

    assert result.exit_code == 0, result.output
    line_with(result.stdout, "Spend", "$0.30")
    line_with(result.stdout, "No Budget")
    assert "Warning" in result.stderr
    assert "Invalid API key" in result.stderr
    assert "higgs auth login" in result.stderr  # the error's hint
    line_with(result.stdout, "balance", "console.higgsfield.ai")


def test_usage_as_text_outside_a_project_shows_only_the_account(higgs, projects_root, api_key, respx_mock, monkeypatch):
    monkeypatch.chdir(projects_root)
    respx_mock.get(USAGE_URL).mock(return_value=httpx.Response(200, json=DOCS_USAGE))

    result = higgs("usage")

    assert result.exit_code == 0, result.output
    assert "Spend" not in result.stdout
    line_with(result.stdout, "Total", "$11.2800")
