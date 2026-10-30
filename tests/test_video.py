"""`higgs video t2v|i2v`: the convenience layer over the video Endpoints.

Expected Endpoint ids and parameter names come from the docs (via `catalog.json`).
"""

import itertools
import json
from typing import Any

import httpx
import pytest
import respx

from hfcore.catalog.loader import load_baked_in

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


def test_a_model_without_the_mode_fails_and_lists_its_modes(higgs, project, api_key, respx_mock):
    # Kling O3 has first/last frame, image and video reference and edit, but no text-to-video.
    result = video(higgs, "t2v", "--model", "kling-o3", "--prompt", "Waves", "--json", "--yes")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "unknown_mode"
    assert "i2v, ref, vref, edit" in error["hint"]
    assert not respx_mock.calls


UPLOAD_URL_ENDPOINT = f"{API}/files/generate-upload-url"
STORAGE_URL = "https://storage.example.com/presigned-upload-url"
# Minimal files that start with the JPEG / PNG magic bytes.
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def mock_uploads(respx_mock: respx.MockRouter) -> None:
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

    respx_mock.post(UPLOAD_URL_ENDPOINT).mock(side_effect=upload_url)


@pytest.fixture
def images(project):
    (project / "inputs" / "first.jpg").write_bytes(JPEG)
    (project / "inputs" / "last.png").write_bytes(PNG)


# (model, quality, endpoint, expected body): each Model names the start/end frame and quality its own way.
I2V_MAPPINGS = [
    (
        "kling-3.0", "std", "kling-video/v3.0/std/image-to-video",
        {"image_url": "https://cdn.example.com/upload-1", "last_image_url": "https://cdn.example.com/upload-2"},
    ),
    (
        "kling-o3", "4k", "kling-video/o3/first-last-frame",
        {
            "first_frame_url": "https://cdn.example.com/upload-1",
            "last_frame_url": "https://cdn.example.com/upload-2",
            "mode": "4k",
        },
    ),
    (
        "seedance-2.0", "1080p", "bytedance/seedance-2.0/image-to-video",
        {
            "image_url": "https://cdn.example.com/upload-1",
            "end_image_url": "https://cdn.example.com/upload-2",
            "resolution": "1080p",
        },
    ),
    (
        "minimax-h3", "2k", "minimax/h3/image-to-video",  # its only resolution is spelled "2K"
        {
            "image_url": "https://cdn.example.com/upload-1",
            "end_image_url": "https://cdn.example.com/upload-2",
            "resolution": "2K",
        },
    ),
]


@pytest.mark.parametrize(("model", "quality", "endpoint", "expected"), I2V_MAPPINGS)
def test_i2v_maps_start_and_end_frames_and_quality_per_model(
    higgs, project, api_key, respx_mock, estimate, images, model, quality, endpoint, expected
):
    mock_uploads(respx_mock)
    route = mock_submit(respx_mock, endpoint)

    result = video(
        higgs, "i2v", "--model", model, "--quality", quality, "--prompt", "She turns around",
        "--image", "inputs/first.jpg", "--end-image", "inputs/last.png", "--no-wait", "--json", "--yes",
    )

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "She turns around", **expected}
    record = json.loads(result.output)[0]["record"]
    assert [item["source"] for item in record["inputs"]] == ["inputs/first.jpg", "inputs/last.png"]


CATALOG = load_baked_in()
T2V_I2V = [endpoint for endpoint in CATALOG.endpoints if endpoint.mode in ("t2v", "i2v")]


@pytest.mark.parametrize("endpoint", T2V_I2V, ids=[endpoint.id for endpoint in T2V_I2V])
def test_every_t2v_and_i2v_endpoint_is_reachable_with_the_common_flags(
    higgs, project, api_key, respx_mock, estimate, images, endpoint
):
    mock_uploads(respx_mock)
    route = mock_submit(respx_mock, endpoint.id)
    args = ["--model", endpoint.model, "--prompt", "A quiet harbour at dawn"]
    if endpoint.tier is not None:
        args += ["--quality", endpoint.tier]
    if endpoint.mode == "i2v":
        args += ["--image", "inputs/first.jpg"]
    if endpoint.parameter("duration").required:
        args += ["--duration", str(endpoint.parameter("duration").default)]

    result = video(higgs, endpoint.mode, *args, "--no-wait", "--json", "--yes")

    assert result.exit_code == 0, result.output
    sent = body(route)
    assert sent["prompt"] == "A quiet harbour at dawn"
    if endpoint.mode == "i2v":
        [image_param] = [name for name, value in sent.items() if value == "https://cdn.example.com/upload-1"]
        assert image_param in {parameter.name for parameter in endpoint.parameters}


def test_a_missing_model_and_tier_are_asked_for_with_their_choices(higgs, project, api_key, respx_mock, estimate):
    route = mock_submit(respx_mock, "kling-video/v3.0/4k/text-to-video")

    result = video(higgs, "t2v", "--prompt", "Waves at dusk", "--no-wait", "--yes", input="kling-3.0\n4k\n")

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "Waves at dusk"}
    assert "seedance-2.0" in result.output  # the Models with t2v are offered
    assert "std, pro, 4k" in result.output  # and then Kling 3.0's tiers


def test_a_missing_prompt_and_required_duration_are_asked_for(higgs, project, api_key, respx_mock, estimate):
    # LTX-2.5 requires a duration of 6, 8 or 10 seconds.
    route = mock_submit(respx_mock, "lightricks/ltx-2.5/text-to-video/fast")

    result = video(
        higgs, "t2v", "--model", "ltx-2.5", "--quality", "fast", "--no-wait", "--yes", input="Waves at dusk\n8\n"
    )

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "Waves at dusk", "duration": 8}
    assert "6, 8, 10" in result.output


def test_a_missing_image_is_asked_for(higgs, project, api_key, respx_mock, estimate, images):
    mock_uploads(respx_mock)
    route = mock_submit(respx_mock, "bytedance/seedance-2.0/image-to-video")

    result = video(higgs, "i2v", "--model", "seedance-2.0", "--no-wait", "--yes", input="inputs/first.jpg\n")

    assert result.exit_code == 0, result.output
    assert body(route) == {"image_url": "https://cdn.example.com/upload-1"}


@pytest.mark.parametrize(
    ("args", "flag"),
    [
        (("t2v", "--prompt", "Waves"), "--model"),
        (("t2v", "--model", "kling-3.0", "--prompt", "Waves"), "--quality"),
        (("t2v", "--model", "seedance-2.0"), "--prompt"),
        (("t2v", "--model", "ltx-2.5", "--quality", "pro", "--prompt", "Waves"), "--duration"),
        (("i2v", "--model", "seedance-2.0", "--prompt", "Waves"), "--image"),
    ],
)
@pytest.mark.parametrize("no_input", ["--json", "--no-input"])
def test_without_input_a_missing_parameter_fails_at_once(higgs, project, api_key, respx_mock, args, flag, no_input):
    result = video(higgs, *args, no_input, "--yes")

    assert result.exit_code == 1
    if no_input == "--json":
        error = json.loads(result.output)["error"]
        assert error["code"] == "missing_param"
        assert error["message"] == f"Missing {flag}"
    else:
        assert f"Missing {flag}" in result.output
    assert not respx_mock.calls


def test_a_flag_the_endpoint_has_no_parameter_for_fails_before_any_call(higgs, project, api_key, respx_mock, images):
    result = video(
        higgs, "i2v", "--model", "kling-2.6", "--prompt", "She turns around",
        "--image", "inputs/first.jpg", "--end-image", "inputs/last.png", "--json", "--yes",
    )

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "unsupported_flag"
    assert error["message"] == "--end-image is not supported by kling-2.6 i2v"
    assert "--image" in error["hint"]
    assert not respx_mock.calls
