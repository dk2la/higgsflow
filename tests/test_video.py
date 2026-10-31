"""`higgs video t2v|i2v`: the convenience layer over the video Endpoints.

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


def write_defaults(project, defaults: str) -> None:
    (project / "project.toml").write_text(f'name = "demo"\n\n{defaults}\n')


def test_project_defaults_fill_the_flags_that_were_not_given(higgs, project, api_key, respx_mock, estimate):
    write_defaults(project, '[defaults]\nmodel = "kling-3.0"\nquality = "pro"\naspect = "9:16"\nduration = 10\n')
    route = mock_submit(respx_mock, "kling-video/v3.0/pro/text-to-video")

    result = video(higgs, "t2v", "--prompt", "Waves at dusk", "--no-wait", "--json", "--yes")

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "Waves at dusk", "duration": 10, "aspect_ratio": "9:16"}


def test_flags_beat_project_defaults(higgs, project, api_key, respx_mock, estimate):
    write_defaults(project, '[defaults]\nmodel = "kling-3.0"\nquality = "pro"\naspect = "9:16"\nduration = 10\n')
    route = mock_submit(respx_mock, "kling-video/v3.0/4k/text-to-video")

    result = video(higgs, "t2v", "--prompt", "Waves", "-q", "4k", "--aspect", "1:1", "--no-wait", "--json", "--yes")

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "Waves", "duration": 10, "aspect_ratio": "1:1"}


def test_defaults_that_do_not_fit_the_endpoint_are_left_out(higgs, project, api_key, respx_mock, estimate):
    # Hailuo 2.3 has no aspect ratio or quality parameter, and lasts 6 or 10 seconds.
    write_defaults(project, '[defaults]\nquality = "pro"\naspect = "16:9"\nduration = 5\n')
    route = mock_submit(respx_mock, "minimax/hailuo-2.3/standard/text-to-video")

    result = video(higgs, "t2v", "--model", "hailuo-2.3", "--prompt", "Waves", "--no-wait", "--json", "--yes")

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "Waves"}


def test_video_defaults_beat_the_shared_ones(higgs, project, api_key, respx_mock, estimate):
    # A shared default Model without the Mode (an image Model) does not count.
    write_defaults(project, '[defaults]\nmodel = "soul-v2"\naspect = "9:16"\n\n[defaults.video]\nmodel = "seedance-2.0"\n')
    route = mock_submit(respx_mock, "bytedance/seedance-2.0/text-to-video")

    result = video(higgs, "t2v", "--prompt", "Waves", "--no-wait", "--json", "--yes")
    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "Waves", "aspect_ratio": "9:16"}

    write_defaults(project, '[defaults]\nmodel = "soul-v2"\n')
    result = video(higgs, "t2v", "--prompt", "Waves", "--no-wait", "--json", "--yes")
    assert json.loads(result.output)["error"]["code"] == "missing_param"


def test_prompt_file_reads_the_prompt_relative_to_the_current_directory(
    higgs, project, api_key, respx_mock, estimate, monkeypatch
):
    (project / "prompts" / "dusk.md").write_text("Waves at dusk,\nslow dolly in.\n")
    monkeypatch.chdir(project / "prompts")
    route = mock_submit(respx_mock, "bytedance/seedance-2.0/text-to-video")

    result = video(
        higgs, "t2v", "--model", "seedance-2.0", "--prompt-file", "dusk.md", "--name", "sunset",
        "--no-wait", "--json", "--yes",
    )

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "Waves at dusk,\nslow dolly in."}
    assert json.loads(result.output)[0]["record"]["naming"]["slug"] == "sunset"


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (("--prompt-file", "prompts/missing.md"), "prompt_file_not_found"),
        (("--prompt-file", "../elsewhere.md"), "prompt_file_outside_project"),
        (("--prompt-file", "prompts/dusk.md", "--prompt", "Waves"), "conflicting_flags"),
    ],
)
def test_a_prompt_file_that_cannot_be_used_fails_before_any_call(higgs, project, api_key, respx_mock, args, code):
    (project / "prompts" / "dusk.md").write_text("Waves at dusk\n")
    (project.parent / "elsewhere.md").write_text("Waves at dusk\n")

    result = video(higgs, "t2v", "--model", "seedance-2.0", *args, "--json", "--yes")

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == code
    assert not respx_mock.calls


WAN_T2V = "wan/v2.7/text-to-video"  # has a seed of 1 to 2147483646


def test_count_submits_separate_requests_with_one_confirmation_and_different_seeds(
    higgs, project, api_key, respx_mock, estimate
):
    route = mock_submit(respx_mock, WAN_T2V)
    first = video(higgs, "t2v", "--model", "wan-2.7", "--prompt", "Waves", "--name", "waves", "--count", "3",
                  "--no-wait", "--json", "--yes")
    assert first.exit_code == 0, first.output
    estimated = len(estimate.calls)

    result = video(higgs, "t2v", "--model", "wan-2.7", "--prompt", "Waves", "--name", "waves", "--count", "3",
                   "--no-wait", input="y\n")

    assert result.exit_code == 0, result.output
    assert result.output.count("Submit 3 Requests for $0.282?") == 1
    assert len(estimate.calls) - estimated == 3
    bodies = [body(route, index) for index in (3, 4, 5)]
    seeds = [sent.pop("seed") for sent in bodies]
    assert len(set(seeds)) == 3
    assert all(1 <= seed <= 2147483646 for seed in seeds)
    assert bodies == [{"prompt": "Waves"}] * 3
    records = [json.loads((project / ".hf" / "requests" / f"req-{n}.json").read_text()) for n in (4, 5, 6)]
    assert [record["naming"]["version"] for record in records] == [4, 5, 6]
    for n in (4, 5, 6):
        assert f"Submitted Request req-{n}" in result.output


def test_count_with_a_seed_counts_up_from_it(higgs, project, api_key, respx_mock, estimate):
    route = mock_submit(respx_mock, WAN_T2V)

    result = video(higgs, "t2v", "--model", "wan-2.7", "--prompt", "Waves", "--seed", "7", "--count", "3",
                   "--no-wait", "--json", "--yes")

    assert result.exit_code == 0, result.output
    assert [body(route, index)["seed"] for index in range(3)] == [7, 8, 9]
    assert [item["request_id"] for item in json.loads(result.output)] == ["req-1", "req-2", "req-3"]


def test_a_single_request_sends_a_seed_only_when_given(higgs, project, api_key, respx_mock, estimate):
    route = mock_submit(respx_mock, WAN_T2V)

    video(higgs, "t2v", "--model", "wan-2.7", "--prompt", "Waves", "--no-wait", "--json", "--yes")
    video(higgs, "t2v", "--model", "wan-2.7", "--prompt", "Waves", "--seed", "42", "--no-wait", "--json", "--yes")

    assert body(route, 0) == {"prompt": "Waves"}
    assert body(route, 1) == {"prompt": "Waves", "seed": 42}


def test_count_on_a_model_without_seeds_repeats_the_same_body(higgs, project, api_key, respx_mock, estimate):
    route = mock_submit(respx_mock, "bytedance/seedance-2.0/text-to-video")

    result = video(higgs, "t2v", "--model", "seedance-2.0", "--prompt", "Waves", "--count", "2",
                   "--no-wait", "--json", "--yes")

    assert result.exit_code == 0, result.output
    assert [body(route, index) for index in range(2)] == [{"prompt": "Waves"}] * 2

    result = video(higgs, "t2v", "--model", "seedance-2.0", "--prompt", "Waves", "--seed", "7", "--json", "--yes")
    assert json.loads(result.output)["error"]["code"] == "unsupported_flag"


def test_count_waits_for_and_downloads_every_request(higgs, project, api_key, respx_mock, estimate):
    mock_submit(respx_mock, WAN_T2V)

    def completed(request: httpx.Request) -> httpx.Response:
        request_id = request.url.path.split("/")[2]
        # A completed video Request ("Requests and lifecycle" → Completed response).
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "request_id": request_id,
                "video": {"url": f"https://cdn.example.com/{request_id}.mp4"},
            },
        )

    respx_mock.get(url__regex=rf"{API}/requests/req-\d+/status").mock(side_effect=completed)
    respx_mock.get(url__regex=r"https://cdn.example.com/req-\d+.mp4").mock(
        side_effect=lambda request: httpx.Response(200, content=request.url.path.encode())
    )

    result = video(higgs, "t2v", "--model", "wan-2.7", "--prompt", "Waves", "--name", "waves", "--count", "2",
                   "--json", "--yes")

    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert [item["record"]["status"] for item in output] == ["completed", "completed"]
    day = project / "generations" / "2026-10-30"
    assert (day / "20261030-1200_wan-2-7_t2v_waves_v01.mp4").read_bytes() == b"/req-1.mp4"
    assert (day / "20261030-1200_wan-2-7_t2v_waves_v02.mp4").read_bytes() == b"/req-2.mp4"
    assert [Path(item["generations"][0]["path"]).name for item in output] == [
        "20261030-1200_wan-2-7_t2v_waves_v01.mp4",
        "20261030-1200_wan-2-7_t2v_waves_v02.mp4",
    ]


def test_a_failed_request_fails_the_command_after_the_others_are_downloaded(
    higgs, project, api_key, respx_mock, estimate
):
    mock_submit(respx_mock, WAN_T2V)
    respx_mock.get(f"{API}/requests/req-1/status").mock(
        return_value=httpx.Response(200, json={"status": "failed", "request_id": "req-1", "error": "Generation failed"})
    )
    respx_mock.get(f"{API}/requests/req-2/status").mock(
        return_value=httpx.Response(
            200, json={"status": "completed", "request_id": "req-2", "video": {"url": "https://cdn.example.com/2.mp4"}}
        )
    )
    respx_mock.get("https://cdn.example.com/2.mp4").mock(return_value=httpx.Response(200, content=b"mp4-2"))

    result = video(higgs, "t2v", "--model", "wan-2.7", "--prompt", "Waves", "--name", "waves", "--count", "2", "--yes")

    assert result.exit_code == 1
    output = " ".join(result.output.split())
    assert "Completed Request req-2" in output
    assert "Request req-1 failed: Generation failed" in output
    day = project / "generations" / "2026-10-30"
    assert (day / "20261030-1200_wan-2-7_t2v_waves_v02.mp4").read_bytes() == b"mp4-2"
    assert json.loads((project / ".hf" / "requests" / "req-1.json").read_text())["status"] == "failed"


def test_json_with_count_reports_every_request_when_one_does_not_complete(
    higgs, project, api_key, respx_mock, estimate
):
    mock_submit(respx_mock, WAN_T2V)

    def status(request: httpx.Request) -> httpx.Response:
        request_id = request.url.path.split("/")[2]
        if request_id == "req-2":
            return httpx.Response(200, json={"status": "nsfw", "request_id": request_id})
        url = f"https://cdn.example.com/{request_id}.mp4"
        return httpx.Response(200, json={"status": "completed", "request_id": request_id, "video": {"url": url}})

    respx_mock.get(url__regex=rf"{API}/requests/req-\d+/status").mock(side_effect=status)
    respx_mock.get(url__regex=r"https://cdn.example.com/req-\d+.mp4").mock(
        side_effect=lambda request: httpx.Response(200, content=request.url.path.encode())
    )

    result = video(higgs, "t2v", "--model", "wan-2.7", "--prompt", "Waves", "--name", "waves", "--count", "3",
                   "--json", "--yes")

    assert result.exit_code == 1
    output = json.loads(result.output)  # one JSON document: every Request, finished or not
    assert [item["request_id"] for item in output] == ["req-1", "req-2", "req-3"]
    assert [item["record"]["status"] for item in output] == ["completed", "nsfw", "completed"]
    assert [Path(output[index]["generations"][0]["path"]).name for index in (0, 2)] == [
        "20261030-1200_wan-2-7_t2v_waves_v01.mp4",
        "20261030-1200_wan-2-7_t2v_waves_v03.mp4",
    ]
    assert "error" not in output[0] and "error" not in output[2]
    assert "generations" not in output[1]
    assert output[1]["error"]["code"] == "request_nsfw"
    assert output[1]["error"]["request_id"] == "req-2"
    assert output[1]["error"]["status"] == "nsfw"


QUEUE_FULL = httpx.Response(
    429,
    headers={"Retry-After": "30"},
    json={"detail": {"code": "account_queue_full", "message": "Too many queued generations", "retry_after_seconds": 30}},
)


def mock_completed_videos(respx_mock) -> None:
    """Every Request completes with a video whose bytes are its own CDN path."""
    respx_mock.get(url__regex=rf"{API}/requests/req-\d+/status").mock(
        side_effect=lambda request: httpx.Response(
            200,
            json={
                "status": "completed",
                "request_id": request.url.path.split("/")[2],
                "video": {"url": f"https://cdn.example.com/{request.url.path.split('/')[2]}.mp4"},
            },
        )
    )
    respx_mock.get(url__regex=r"https://cdn.example.com/req-\d+.mp4").mock(
        side_effect=lambda request: httpx.Response(200, content=request.url.path.encode())
    )


def test_a_submit_giving_up_mid_count_still_reports_and_downloads_what_was_submitted(
    higgs, project, api_key, respx_mock, estimate
):
    # The first submit is accepted; then the queue stays full and the second gives up.
    respx_mock.post(f"{API}/{WAN_T2V}").mock(side_effect=[submitted("req-1"), *[QUEUE_FULL] * 5])
    mock_completed_videos(respx_mock)

    result = video(higgs, "t2v", "--model", "wan-2.7", "--prompt", "Waves", "--name", "waves", "--count", "3",
                   "--json", "--yes")

    assert result.exit_code == 1
    output = json.loads(result.output)  # one item per requested Request
    assert [item["request_id"] for item in output] == ["req-1", None, None]
    assert output[0]["record"]["status"] == "completed"
    assert Path(output[0]["generations"][0]["path"]).name == "20261030-1200_wan-2-7_t2v_waves_v01.mp4"
    for item in output[1:]:
        error = item["error"]
        assert error["code"] == "queue_full"
        # Not "nothing was charged": req-1 went through and is billed once it completes.
        assert "Nothing was submitted" not in error["hint"]
        assert "1 of the 3 Requests" in error["hint"] and "req-1" in error["hint"]
        assert "2 were not submitted" in error["hint"]


def test_a_submit_failing_on_the_network_mid_count_says_that_request_may_have_been_submitted(
    higgs, project, api_key, respx_mock, estimate
):
    # The second submit times out every time: it may have reached the API, the third never ran.
    respx_mock.post(f"{API}/{WAN_T2V}").mock(side_effect=[submitted("req-1"), *[httpx.ReadTimeout("timed out")] * 5])
    mock_completed_videos(respx_mock)

    result = video(higgs, "t2v", "--model", "wan-2.7", "--prompt", "Waves", "--count", "3", "--json", "--yes")

    assert result.exit_code == 1
    output = json.loads(result.output)
    assert [item["request_id"] for item in output] == ["req-1", None, None]
    hint = output[1]["error"]["hint"]
    assert "may have reached the API" in hint and "Idempotency-Key" in hint
    assert "1 of the 3 Requests was submitted before this (req-1)" in hint
    assert "this one may have been submitted too (see above), and the other 1 was not submitted" in hint
    assert "2 were not submitted" not in hint


@pytest.mark.parametrize(
    ("failure", "says"),
    [
        (QUEUE_FULL, "None of the 3 Requests was submitted."),
        (httpx.ReadTimeout("timed out"), "The other 2 Requests were not submitted."),
    ],
)
def test_a_first_submit_giving_up_says_what_it_means_for_the_count(
    higgs, project, api_key, respx_mock, estimate, failure, says
):
    respx_mock.post(f"{API}/{WAN_T2V}").mock(side_effect=[failure] * 5)

    result = video(higgs, "t2v", "--model", "wan-2.7", "--prompt", "Waves", "--count", "3", "--json", "--yes")

    assert result.exit_code == 1
    hint = json.loads(result.output)["error"]["hint"]
    assert says in hint
    # A timed-out submit may exist, so it must not be counted among the unsubmitted.
    assert ("None of the" in hint) == (failure is QUEUE_FULL)


def test_a_submit_giving_up_mid_count_as_text_names_what_was_submitted(higgs, project, api_key, respx_mock, estimate):
    respx_mock.post(f"{API}/{WAN_T2V}").mock(side_effect=[submitted("req-1"), *[QUEUE_FULL] * 5])

    result = video(higgs, "t2v", "--model", "wan-2.7", "--prompt", "Waves", "--count", "2", "--no-wait", "--yes")

    assert result.exit_code == 1
    output = " ".join(result.output.split())
    assert "Submitted Request req-1" in output
    assert "1 of the 2 Requests" in output and "req-1" in output


def test_polling_giving_up_on_one_request_of_a_count_still_downloads_the_others(
    higgs, project, api_key, respx_mock, estimate
):
    mock_submit(respx_mock, WAN_T2V)
    respx_mock.get(f"{API}/requests/req-1/status").mock(
        return_value=httpx.Response(503, json={"detail": {"code": "admission_control_unavailable", "message": "x"}})
    )
    mock_completed_videos(respx_mock)

    result = video(higgs, "t2v", "--model", "wan-2.7", "--prompt", "Waves", "--name", "waves", "--count", "2",
                   "--json", "--yes")

    assert result.exit_code == 1
    output = json.loads(result.output)
    assert [item["request_id"] for item in output] == ["req-1", "req-2"]
    assert output[0]["error"]["code"] == "service_unavailable"
    assert "higgs sync" in output[0]["error"]["hint"]
    assert "billed only if it completes" in output[0]["error"]["hint"]
    assert output[1]["record"]["status"] == "completed"
    assert Path(output[1]["generations"][0]["path"]).name == "20261030-1200_wan-2-7_t2v_waves_v02.mp4"


def test_p_parameters_reach_the_body_and_win_over_flags(higgs, project, api_key, respx_mock, estimate):
    route = mock_submit(respx_mock, "kling-video/v2.6/pro/text-to-video")

    result = video(
        higgs, "t2v", "--model", "kling-2.6", "--prompt", "Waves", "--aspect", "9:16",
        "-p", "sound=off", "-p", "aspect_ratio=1:1", "--no-wait", "--json", "--yes",
    )

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "Waves", "sound": "off", "aspect_ratio": "1:1"}


def test_an_image_can_be_a_reference(higgs, project, api_key, respx_mock, estimate):
    (project / "references" / "hero.jpg").write_bytes(JPEG)
    mock_uploads(respx_mock)
    route = mock_submit(respx_mock, "kling-video/v2.6/pro/image-to-video")

    result = video(
        higgs, "i2v", "--model", "kling-2.6", "--prompt", "Hero waves", "--image", "@hero", "--no-wait", "--json", "--yes"
    )

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "Hero waves", "image_url": "https://cdn.example.com/upload-1"}
    assert json.loads(result.output)[0]["record"]["inputs"][0]["source"] == "@hero"


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
