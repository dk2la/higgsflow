"""`higgs video t2v|i2v`: the convenience layer over the video Endpoints.

Expected Endpoint ids and parameter names come from the docs (via `catalog.json`).
"""

import itertools
import json
from typing import Any

import httpx
import pytest

API = "https://api.higgsfield.ai"


def submitted(request_id: str) -> httpx.Response:
    # The submit response from the docs ("Requests and lifecycle" → Initial response).
    return httpx.Response(
        200,
        json={
            "status": "queued",
            "request_id": request_id,
            "status_url": f"{API}/requests/{request_id}/status",
            "cancel_url": f"{API}/requests/{request_id}/cancel",
            "expires_after": 604800,
        },
    )


def mock_submit(respx_mock, endpoint: str):
    """Accept every submit to `endpoint`, each with a new request id (req-1, req-2, ...)."""
    counter = itertools.count(1)
    return respx_mock.post(f"{API}/{endpoint}").mock(side_effect=lambda request: submitted(f"req-{next(counter)}"))


def body(route, index: int = -1) -> dict[str, Any]:
    return json.loads(route.calls[index].request.content)


def video(higgs, *args: str, input: str | None = None):
    return higgs("video", *args, input=input)


def test_t2v_maps_the_common_flags_onto_the_endpoint_of_the_model_and_tier(
    higgs, project, api_key, respx_mock, estimate
):
    route = mock_submit(respx_mock, "kling-video/v3.0/pro/text-to-video")

    result = video(
        higgs, "t2v", "--model", "kling-3.0", "--quality", "pro", "--prompt", "Waves at dusk",
        "--duration", "10", "--aspect", "9:16", "--no-wait", "--json", "--yes",
    )

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "Waves at dusk", "duration": 10, "aspect_ratio": "9:16"}
    output = json.loads(result.output)
    assert [item["request_id"] for item in output] == ["req-1"]
    assert output[0]["record"]["endpoint"] == "kling-video/v3.0/pro/text-to-video"
    assert (project / ".hf" / "requests" / "req-1.json").is_file()
