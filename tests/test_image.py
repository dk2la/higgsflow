"""`higgs image t2i|i2i`: the convenience layer over the image Endpoints.

Expected Endpoint ids and parameter names come from the docs (via `catalog.json`).
"""

import itertools
import json
from pathlib import Path
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


def test_ads_studio_is_not_an_image_mode(higgs, project, api_key, respx_mock):
    result = image(higgs, "t2i", "--model", "ads-studio", "--prompt", "Sneaker ad", "--json", "--yes")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "unknown_mode"
    assert error["hint"] == "Its modes: ads"
    assert not respx_mock.calls


def mock_completed_images(respx_mock, images_per_request: int) -> None:
    """Every Request completes with `images_per_request` images, each served as its own URL path."""

    def completed(request: httpx.Request) -> httpx.Response:
        request_id = request.url.path.split("/")[2]
        # The image output from the docs ("Requests and lifecycle" → Completed output).
        urls = [f"https://cdn.example.com/{request_id}/image-{n}.png" for n in range(1, images_per_request + 1)]
        return httpx.Response(
            200, json={"status": "completed", "request_id": request_id, "images": [{"url": url} for url in urls]}
        )

    respx_mock.get(url__regex=rf"{API}/requests/req-\d+/status").mock(side_effect=completed)
    respx_mock.get(url__regex=r"https://cdn.example.com/req-\d+/image-\d+.png").mock(
        side_effect=lambda request: httpx.Response(200, content=request.url.path.encode())
    )


SOUL_V2 = "higgsfield-ai/soul/v2/standard"  # batch_size 1 or 4, seed 1 to 1000000


def test_batch_size_gives_numbered_generations_from_one_request(higgs, project, api_key, respx_mock, estimate):
    route = mock_submit(respx_mock, SOUL_V2)
    mock_completed_images(respx_mock, 4)

    result = image(
        higgs, "t2i", "-m", "soul-v2", "--prompt", "Editorial portrait", "--name", "portrait",
        "-p", "batch_size=4", "--json", "--yes",
    )

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "Editorial portrait", "batch_size": 4}
    [output] = json.loads(result.output)
    names = [Path(item["path"]).name for item in output["generations"]]
    assert names == [f"20261030-1200_soul-v2_t2i_portrait_v01-{n}.png" for n in (1, 2, 3, 4)]
    day = project / "generations" / "2026-10-30"
    assert [(day / name).read_bytes() for name in names] == [f"/req-1/image-{n}.png".encode() for n in (1, 2, 3, 4)]


def test_count_and_batch_size_combine(higgs, project, api_key, respx_mock, estimate):
    route = mock_submit(respx_mock, SOUL_V2)
    mock_completed_images(respx_mock, 4)

    result = image(
        higgs, "t2i", "-m", "soul-v2", "--prompt", "Editorial portrait", "--name", "portrait",
        "--count", "2", "--seed", "7", "-p", "batch_size=4", input="y\n",
    )

    assert result.exit_code == 0, result.output
    assert result.output.count("Submit 2 Requests for $0.188?") == 1
    assert [body(route, index) for index in (0, 1)] == [
        {"prompt": "Editorial portrait", "seed": 7, "batch_size": 4},
        {"prompt": "Editorial portrait", "seed": 8, "batch_size": 4},
    ]
    day = project / "generations" / "2026-10-30"
    for version, request_id in (("v01", "req-1"), ("v02", "req-2")):
        for n in (1, 2, 3, 4):
            name = f"20261030-1200_soul-v2_t2i_portrait_{version}-{n}.png"
            assert (day / name).read_bytes() == f"/{request_id}/image-{n}.png".encode()
    assert len(list(day.glob("*.png"))) == 8


def write_defaults(project, defaults: str) -> None:
    (project / "project.toml").write_text(f'name = "demo"\n\n{defaults}\n')


def test_image_defaults_beat_the_shared_ones(higgs, project, api_key, respx_mock, estimate):
    # The shared default Model is a video Model, so it does not count for images.
    write_defaults(
        project,
        '[defaults]\nmodel = "kling-3.0"\naspect = "16:9"\nduration = 5\n\n'
        '[defaults.image]\nmodel = "soul-v2"\nquality = "1080p"\n',
    )
    route = mock_submit(respx_mock, SOUL_V2)

    result = image(higgs, "t2i", "--prompt", "Editorial portrait", "--no-wait", "--json", "--yes")

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "Editorial portrait", "aspect_ratio": "16:9", "resolution": "1080p"}


def test_a_missing_model_is_asked_for_among_the_models_of_the_mode(higgs, project, api_key, respx_mock, estimate):
    route = mock_submit(respx_mock, "z-image/turbo")

    result = image(higgs, "t2i", "--prompt", "A lighthouse", "--no-wait", "--yes", input="z-image-turbo\n")

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "A lighthouse"}
    assert "recraft-v4.1" in result.output  # the Models with t2i are offered
    assert "kling-3.0" not in result.output


@pytest.mark.parametrize(
    ("args", "flag"),
    [
        (("t2i", "--prompt", "A lighthouse"), "--model"),
        (("t2i", "--model", "recraft-v4.1"), "--prompt"),
        (("i2i", "--model", "qwen-image-3", "--prompt", "Make it night"), "--image"),
    ],
)
def test_without_input_a_missing_parameter_fails_at_once(higgs, project, api_key, respx_mock, args, flag):
    result = image(higgs, *args, "--json", "--yes")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "missing_param"
    assert error["message"] == f"Missing {flag}"
    assert not respx_mock.calls


def test_a_missing_input_image_is_asked_for(higgs, project, api_key, respx_mock, estimate, images):
    mock_uploads(respx_mock)
    route = mock_submit(respx_mock, "alibaba/qwen-image-3/edit")

    result = image(
        higgs, "i2i", "--model", "qwen-image-3", "--prompt", "Make it night", "--no-wait", "--yes",
        input="inputs/a.jpg\n",
    )

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "Make it night", "image_urls": [UPLOAD_1]}


def test_an_image_can_be_a_reference_and_p_parameters_win(higgs, project, api_key, respx_mock, estimate):
    (project / "references" / "hero.jpg").write_bytes(JPEG)
    mock_uploads(respx_mock)
    route = mock_submit(respx_mock, "higgsfield-ai/soul/standard")

    result = image(
        higgs, "t2i", "--model", "soul", "--prompt", "Hero portrait", "--image", "@hero", "--aspect", "1:1",
        "-p", "aspect_ratio=3:4", "-p", "style_strength=0.5", "--no-wait", "--json", "--yes",
    )

    assert result.exit_code == 0, result.output
    assert body(route) == {
        "prompt": "Hero portrait", "image_reference_url": UPLOAD_1, "aspect_ratio": "3:4", "style_strength": 0.5,
    }
    assert json.loads(result.output)[0]["record"]["inputs"][0]["source"] == "@hero"


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


def test_an_unsupported_flag_lists_the_flags_of_the_model_once(higgs, project, api_key, respx_mock):
    result = image(higgs, "t2i", "--model", "ideogram-4.0", "--prompt", "A lighthouse", "--seed", "7", "--json", "--yes")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["message"] == "--seed is not supported by ideogram-4.0 t2i"
    assert error["hint"].startswith("Its flags: --prompt, --image, --quality, --aspect; ")


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
