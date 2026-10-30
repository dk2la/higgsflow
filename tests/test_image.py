"""`higgs image t2i|i2i`: the convenience layer over the image Endpoints.

Expected Endpoint ids and parameter names come from the docs (via `catalog.json`).
"""

import itertools
import json
from typing import Any

import httpx

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
    sent: dict[str, Any] = json.loads(route.calls[index].request.content)
    return sent


def image(higgs, *args: str, input: str | None = None):
    return higgs("image", *args, input=input)


def test_t2i_maps_the_common_flags_onto_the_endpoint_of_the_model(higgs, project, api_key, respx_mock, estimate):
    route = mock_submit(respx_mock, "higgsfield-ai/soul/v2/standard")

    result = image(
        higgs, "t2i", "--model", "soul-v2", "--prompt", "Editorial portrait", "--aspect", "9:16",
        "--quality", "1080p", "--seed", "42", "--no-wait", "--json", "--yes",
    )

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "Editorial portrait", "aspect_ratio": "9:16", "resolution": "1080p", "seed": 42}
    output = json.loads(result.output)
    assert [item["request_id"] for item in output] == ["req-1"]
    assert output[0]["record"]["endpoint"] == "higgsfield-ai/soul/v2/standard"
    assert (project / ".hf" / "requests" / "req-1.json").is_file()
