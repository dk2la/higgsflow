"""`higgs image t2i|i2i`: the convenience layer over the image Endpoints.

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
    (project / "inputs" / "a.jpg").write_bytes(JPEG)
    (project / "inputs" / "b.png").write_bytes(PNG)


UPLOAD_1 = "https://cdn.example.com/upload-1"
UPLOAD_2 = "https://cdn.example.com/upload-2"

# (model, mode, endpoint, --image values, expected body): each Model names its input images its own way.
IMAGE_MAPPINGS = [
    (
        "grok-image-2.0", "t2i", "xai/grok-imagine-image-2.0", ["inputs/a.jpg", "inputs/b.png"],
        {"image_urls": [UPLOAD_1, UPLOAD_2]},
    ),
    ("marketing-studio-image-2.5-flare", "t2i", "marketing-studio/image/flare", ["inputs/a.jpg"], {"image_urls": [UPLOAD_1]}),
    ("ideogram-4.0", "t2i", "ideogram/v4.0", ["inputs/a.jpg"], {"image_url": UPLOAD_1}),
    ("soul", "t2i", "higgsfield-ai/soul/standard", ["inputs/a.jpg"], {"image_reference_url": UPLOAD_1}),
    (
        "qwen-image-3", "i2i", "alibaba/qwen-image-3/edit", ["inputs/a.jpg", "inputs/b.png"],
        {"image_urls": [UPLOAD_1, UPLOAD_2]},
    ),
]


@pytest.mark.parametrize(("model", "mode", "endpoint", "values", "expected"), IMAGE_MAPPINGS)
def test_image_maps_the_input_images_per_model(
    higgs, project, api_key, respx_mock, estimate, images, model, mode, endpoint, values, expected
):
    mock_uploads(respx_mock)
    route = mock_submit(respx_mock, endpoint)
    args = [arg for value in values for arg in ("--image", value)]

    result = image(higgs, mode, "--model", model, "--prompt", "In this style", *args, "--no-wait", "--json", "--yes")

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "In this style", **expected}
    record = json.loads(result.output)[0]["record"]
    assert [item["source"] for item in record["inputs"]] == values


CATALOG = load_baked_in()
T2I_I2I = [endpoint for endpoint in CATALOG.endpoints if endpoint.mode in ("t2i", "i2i")]
IMAGE_PARAMS = {"image_urls", "image_url", "image_reference_url"}


def test_the_image_modes_cover_every_image_endpoint_but_ads_studio():
    # DESIGN.md §6: 14 t2i Endpoints and Qwen Image 3 edit (i2i); Ads Studio is reachable only via higgs gen.
    assert len([endpoint for endpoint in T2I_I2I if endpoint.mode == "t2i"]) == 14
    assert [endpoint.id for endpoint in T2I_I2I if endpoint.mode == "i2i"] == ["alibaba/qwen-image-3/edit"]


@pytest.mark.parametrize("endpoint", T2I_I2I, ids=[endpoint.id for endpoint in T2I_I2I])
def test_every_t2i_and_i2i_endpoint_is_reachable_with_the_common_flags(
    higgs, project, api_key, respx_mock, estimate, images, endpoint
):
    mock_uploads(respx_mock)
    route = mock_submit(respx_mock, endpoint.id)
    aspects = endpoint.parameter("aspect_ratio").enum
    args = ["--model", endpoint.model, "--prompt", "A quiet harbour at dawn", "--aspect", str(aspects[-1])]
    takes_images = any(parameter.name in IMAGE_PARAMS for parameter in endpoint.parameters)
    if takes_images:
        args += ["--image", "inputs/a.jpg"]

    result = image(higgs, endpoint.mode, *args, "--no-wait", "--json", "--yes")

    assert result.exit_code == 0, result.output
    sent = body(route)
    assert sent["prompt"] == "A quiet harbour at dawn"
    assert sent["aspect_ratio"] == aspects[-1]
    if takes_images:
        [image_param] = [name for name in IMAGE_PARAMS if name in sent]
        assert sent[image_param] in (UPLOAD_1, [UPLOAD_1])


def test_several_images_on_a_model_that_takes_one_fail_before_any_call(higgs, project, api_key, respx_mock, images):
    result = image(
        higgs, "t2i", "--model", "ideogram-4.0", "--prompt", "In this style",
        "--image", "inputs/a.jpg", "--image", "inputs/b.png", "--json", "--yes",
    )

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "too_many_values"
    assert error["message"] == "--image takes one value on ideogram-4.0 t2i, got 2"
    assert not respx_mock.calls


def test_image_is_not_supported_by_a_model_without_input_images(higgs, project, api_key, respx_mock, images):
    result = image(
        higgs, "t2i", "--model", "z-image-turbo", "--prompt", "In this style", "--image", "inputs/a.jpg", "--json", "--yes"
    )

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "unsupported_flag"
    assert error["message"] == "--image is not supported by z-image-turbo t2i"
    assert not respx_mock.calls


# (model, endpoint, --quality, expected body): each Model names its quality its own way.
QUALITY_MAPPINGS = [
    ("grok-image-2.0", "xai/grok-imagine-image-2.0", "medium", {"quality": "medium"}),
    ("grok-image-2.0", "xai/grok-imagine-image-2.0", "2k", {"resolution": "2k"}),
    ("marketing-studio-image-2.5-sunburst", "marketing-studio/image/sunburst", "xhigh", {"quality": "xhigh"}),
    ("marketing-studio-image-2.5-sunburst", "marketing-studio/image/sunburst", "4K", {"resolution": "4k"}),
    ("soul-cinema", "higgsfield-ai/soul/cinema", "1080p", {"resolution": "1080p"}),
    ("ideogram-4.0", "ideogram/v4.0", "turbo", {"rendering_speed": "TURBO"}),
]


@pytest.mark.parametrize(("model", "endpoint", "quality", "expected"), QUALITY_MAPPINGS)
def test_quality_maps_onto_the_quality_resolution_or_rendering_speed_of_the_model(
    higgs, project, api_key, respx_mock, estimate, model, endpoint, quality, expected
):
    route = mock_submit(respx_mock, endpoint)

    result = image(higgs, "t2i", "-m", model, "--prompt", "A lighthouse", "-q", quality, "--no-wait", "--json", "--yes")

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "A lighthouse", **expected}
