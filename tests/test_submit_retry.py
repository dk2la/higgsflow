"""Retrying an ambiguous submit with the same Idempotency-Key (docs "Idempotent requests")."""

import json

import httpx
import pytest

# Every test submits, and every submit is preceded by an estimate.
pytestmark = pytest.mark.usefixtures("estimate")

ENDPOINT = "higgsfield-ai/soul/v2/standard"
SUBMIT_URL = f"https://api.higgsfield.ai/{ENDPOINT}"
REQUEST_ID = "d7e6c0f3-6699-4f6c-bb45-2ad7fd9158ff"
SUBMITTED = {"status": "queued", "request_id": REQUEST_ID}
QUEUE_FULL = {
    "detail": {
        "code": "account_queue_full",
        "message": "Too many queued generations for this account",
        "retryable": True,
        "limit": 40,
        "retry_after_seconds": 30,
    }
}


def gen(higgs, *args: str):
    return higgs("gen", ENDPOINT, "-p", "prompt=a cat", "--no-validate", "--no-wait", "--json", "--yes", *args)


def keys(route) -> list[str]:
    return [call.request.headers["Idempotency-Key"] for call in route.calls]


def test_a_submit_that_times_out_is_retried_with_the_same_key(higgs, project, api_key, respx_mock, clock):
    route = respx_mock.post(SUBMIT_URL).mock(
        side_effect=[httpx.ConnectError("refused"), httpx.ReadTimeout("timed out"), httpx.Response(200, json=SUBMITTED)]
    )

    result = gen(higgs)

    assert result.exit_code == 0, result.output
    assert route.call_count == 3
    assert len(set(keys(route))) == 1
    assert len(clock.waits) == 2 and 2.0 <= clock.waits[0] <= 2.5
    record = json.loads((project / ".hf" / "requests" / f"{REQUEST_ID}.json").read_text())
    assert record["idempotency_key"] == keys(route)[0]


def test_a_submit_that_keeps_failing_gives_up_after_a_few_attempts(higgs, project, api_key, respx_mock, clock):
    route = respx_mock.post(SUBMIT_URL).mock(side_effect=httpx.ConnectError("refused"))

    result = gen(higgs)

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "network_error"
    assert 1 < route.call_count <= 6
    assert len(set(keys(route))) == 1
    assert not list((project / ".hf" / "requests").iterdir())


def test_a_full_queue_on_submit_waits_for_retry_after_and_resubmits(higgs, project, api_key, respx_mock, clock):
    route = respx_mock.post(SUBMIT_URL).mock(
        side_effect=[
            httpx.Response(429, json=QUEUE_FULL, headers={"Retry-After": "30"}),
            httpx.Response(200, json=SUBMITTED),
        ]
    )

    result = gen(higgs)

    assert result.exit_code == 0, result.output
    assert clock.waits == [30.0]
    assert len(set(keys(route))) == 1


def test_a_disabled_model_is_not_retried(higgs, project, api_key, respx_mock, clock):
    route = respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(503, json={"detail": "Model is disabled"}))

    result = gen(higgs)

    assert json.loads(result.output)["error"]["code"] == "model_unavailable"
    assert route.call_count == 1
    assert clock.waits == []


def test_invalid_parameters_are_not_retried(higgs, project, api_key, respx_mock, clock):
    body = {"detail": [{"loc": ["body", "prompt"], "msg": "Field required", "type": "missing"}]}
    route = respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(422, json=body))

    result = gen(higgs)

    assert json.loads(result.output)["error"]["code"] == "invalid_params"
    assert route.call_count == 1
