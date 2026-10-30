"""`higgs video ref|vref|edit|extend|motion|restyle`: the remaining video Modes.

Expected Endpoint ids and parameter names come from the docs (via `catalog.json`).
"""

import json

import pytest

from hfcore.catalog.loader import load_baked_in
from tests.test_video import JPEG, PNG, body, mock_submit, mock_uploads, video

# Minimal files that start with the MP4 / WAV magic bytes.
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 32
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32


def upload(n: int) -> str:
    return f"https://cdn.example.com/upload-{n}"


@pytest.fixture
def media(project):
    inputs = project / "inputs"
    (inputs / "clip.mp4").write_bytes(MP4)
    (inputs / "voice.wav").write_bytes(WAV)
    (inputs / "hero.jpg").write_bytes(JPEG)
    (inputs / "sofa.png").write_bytes(PNG)


def test_edit_sends_the_video_to_the_endpoint(higgs, project, api_key, respx_mock, estimate, media):
    mock_uploads(respx_mock)
    route = mock_submit(respx_mock, "bytedance/seedance-2.5/video-edit")

    result = video(
        higgs, "edit", "--model", "seedance-2.5", "--video", "inputs/clip.mp4", "--prompt", "Make it snow",
        "--no-wait", "--json", "--yes",
    )

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "Make it snow", "video_url": upload(1)}
    record = json.loads(result.output)[0]["record"]
    assert [(item["param"], item["source"]) for item in record["inputs"]] == [("video_url", "inputs/clip.mp4")]


def test_edit_takes_reference_images_and_a_video_list_where_the_endpoint_has_one(
    higgs, project, api_key, respx_mock, estimate, media
):
    # Kling O3 video edit takes `video_urls` and `image_urls`, and `mode` for quality.
    mock_uploads(respx_mock)
    route = mock_submit(respx_mock, "kling-video/o3/video-edit")

    result = video(
        higgs, "edit", "--model", "kling-o3", "--video", "inputs/clip.mp4", "--ref", "inputs/hero.jpg",
        "--ref", "inputs/sofa.png", "--quality", "4k", "--prompt", "Put the hero on the sofa",
        "--no-wait", "--json", "--yes",
    )

    assert result.exit_code == 0, result.output
    assert body(route) == {
        "prompt": "Put the hero on the sofa",
        "video_urls": [upload(1)],
        "image_urls": [upload(2), upload(3)],
        "mode": "4k",
    }


# (args, endpoint, expected body without the prompt): each Model takes its references its own way.
REF_MAPPINGS = [
    (
        (
            "--model", "seedance-2.0", "--ref", "inputs/hero.jpg", "--ref", "inputs/sofa.png",
            "--video", "inputs/clip.mp4", "--audio", "inputs/voice.wav",
            "--quality", "1080p", "--duration", "10", "--aspect", "9:16",
        ),
        "bytedance/seedance-2.0/reference-to-video",
        {
            "image_urls": [upload(1), upload(2)],
            "video_urls": [upload(3)],
            "audio_urls": [upload(4)],
            "resolution": "1080p",
            "duration": 10,
            "aspect_ratio": "9:16",
        },
    ),
    # Grok Imagine takes a single image and audio besides its reference images.
    (
        (
            "--model", "grok-imagine-video-1.5", "--image", "inputs/hero.jpg", "--ref", "inputs/sofa.png",
            "--audio", "inputs/voice.wav",
        ),
        "xai/grok-imagine-video/v1.5/reference-to-video",
        {"image_url": upload(1), "image_urls": [upload(2)], "audio_url": upload(3)},
    ),
    # Kling O3 image reference: references between a first and a last frame.
    (
        (
            "--model", "kling-o3", "--image", "inputs/hero.jpg", "--end-image", "inputs/sofa.png",
            "--ref", "inputs/hero.jpg", "--quality", "pro",
        ),
        "kling-video/o3/image-reference",
        {"first_frame_url": upload(1), "last_frame_url": upload(2), "image_urls": [upload(1)], "mode": "pro"},
    ),
]


@pytest.mark.parametrize(("args", "endpoint", "expected"), REF_MAPPINGS)
def test_ref_maps_the_references_per_model(
    higgs, project, api_key, respx_mock, estimate, media, args, endpoint, expected
):
    mock_uploads(respx_mock)
    route = mock_submit(respx_mock, endpoint)

    result = video(higgs, "ref", *args, "--prompt", "Hero on the sofa", "--no-wait", "--json", "--yes")

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "Hero on the sofa", **expected}


@pytest.mark.parametrize(
    ("mode", "args", "endpoint", "expected"),
    [
        (
            "extend",
            ("--model", "seedance-2.5", "--ref", "inputs/hero.jpg", "--audio", "inputs/voice.wav",
             "--duration", "8", "--quality", "1080p"),
            "bytedance/seedance-2.5/video-extend",
            {
                "video_url": upload(1),
                "image_urls": [upload(2)],
                "audio_urls": [upload(3)],
                "duration": 8,
                "resolution": "1080p",
            },
        ),
        (
            "vref",
            ("--model", "kling-omni", "--ref", "inputs/hero.jpg", "--duration", "10", "--aspect", "9:16",
             "--quality", "std"),
            "kling-video/omni/video-reference",
            {
                "video_urls": [upload(1)],
                "image_urls": [upload(2)],
                "duration": 10,
                "aspect_ratio": "9:16",
                "mode": "std",
            },
        ),
        (
            "edit",
            ("--model", "seedance-2.5", "--audio", "inputs/voice.wav", "--quality", "480p"),
            "bytedance/seedance-2.5/video-edit",
            {"video_url": upload(1), "audio_urls": [upload(2)], "resolution": "480p"},
        ),
    ],
)
def test_the_video_modes_take_the_common_flags_their_endpoints_have(
    higgs, project, api_key, respx_mock, estimate, media, mode, args, endpoint, expected
):
    mock_uploads(respx_mock)
    route = mock_submit(respx_mock, endpoint)

    result = video(
        higgs, mode, "--video", "inputs/clip.mp4", *args, "--prompt", "Night falls", "--no-wait", "--json", "--yes"
    )

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "Night falls", **expected}


@pytest.mark.parametrize(
    ("args", "endpoint", "expected"),
    [
        (
            ("--model", "kling-3.0-motion-control", "--quality", "pro", "-p", "character_orientation=image"),
            "kling-video/v3/motion-control/pro",
            {"video_url": upload(1), "image_url": upload(2), "character_orientation": "image"},
        ),
        # Genjutsu motion transfer takes the character as a list of images.
        (
            ("--model", "genjutsu", "--quality", "1080p"),
            "higgsfield/genjutsu/motion-transfer/v1.0",
            {"video_url": upload(1), "image_urls": [upload(2)], "resolution": "1080p"},
        ),
    ],
)
def test_motion_moves_the_character_of_the_image_like_the_video(
    higgs, project, api_key, respx_mock, estimate, media, args, endpoint, expected
):
    mock_uploads(respx_mock)
    route = mock_submit(respx_mock, endpoint)

    result = video(
        higgs, "motion", *args, "--video", "inputs/clip.mp4", "--image", "inputs/hero.jpg",
        "--no-wait", "--json", "--yes",
    )

    assert result.exit_code == 0, result.output
    assert body(route) == expected


CATALOG = load_baked_in()
NEW_MODES = ("ref", "vref", "edit", "extend", "motion", "restyle")
ENDPOINTS = [endpoint for endpoint in CATALOG.endpoints if endpoint.mode in NEW_MODES]
# The inputs each Mode is about.
MODE_INPUTS = {
    "ref": ("--ref", "inputs/hero.jpg"),
    "vref": ("--video", "inputs/clip.mp4"),
    "edit": ("--video", "inputs/clip.mp4"),
    "extend": ("--video", "inputs/clip.mp4"),
    "motion": ("--video", "inputs/clip.mp4", "--image", "inputs/hero.jpg"),
    "restyle": ("--video", "inputs/clip.mp4"),
}
# Endpoints that need other inputs: Wan 2.6's references are videos, Genjutsu object swap
# needs the object to put in, restyle a Preset.
ENDPOINT_INPUTS = {
    "wan/v2.6/reference-to-video": ("--video", "inputs/clip.mp4"),
    "higgsfield/genjutsu/object-swap/v1.0": ("--video", "inputs/clip.mp4", "--ref", "inputs/sofa.png"),
    "higgsfield/genjutsu/restyle/v1.0": (
        "--video", "inputs/clip.mp4", "-p", "preset_id=0b8a5e7c-2d4f-4f7e-9a51-3c6d2e1f0a9b",
    ),
}


def test_the_new_modes_cover_endpoints_of_every_video_family_but_none_other():
    assert len(ENDPOINTS) == 26  # ref 13, motion 5, edit 4, vref 2, extend 1, restyle 1
    video_modes = {endpoint.mode for endpoint in CATALOG.endpoints if endpoint.kind == "video"}
    assert video_modes == {"t2v", "i2v", *NEW_MODES}


@pytest.mark.parametrize("mode", NEW_MODES)
def test_every_video_mode_is_its_own_subcommand(higgs, mode):
    result = higgs("video", mode, "--help")

    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=[endpoint.id for endpoint in ENDPOINTS])
def test_every_endpoint_of_the_new_modes_is_reachable_with_the_common_flags(
    higgs, project, api_key, respx_mock, estimate, media, endpoint
):
    mock_uploads(respx_mock)
    route = mock_submit(respx_mock, endpoint.id)
    args = ["--model", endpoint.model, "--prompt", "A quiet harbour at dawn"]
    if endpoint.tier is not None:
        args += ["--quality", endpoint.tier]
    inputs = ENDPOINT_INPUTS.get(endpoint.id, MODE_INPUTS[endpoint.mode])
    files = [arg for arg in inputs if arg.startswith("inputs/")]

    result = video(higgs, endpoint.mode, *args, *inputs, "--no-wait", "--json", "--yes")

    assert result.exit_code == 0, result.output
    sent = body(route)
    assert sent["prompt"] == "A quiet harbour at dawn"
    required = {parameter.name for parameter in endpoint.parameters if parameter.required}
    assert required <= set(sent)
    # Every input reached a parameter of its own kind (`video_url`, `image_urls`, ...).
    record = json.loads(result.output)[0]["record"]
    assert [item["source"] for item in record["inputs"]] == files
    for item in record["inputs"]:
        kind = "video" if item["source"].endswith(".mp4") else "image"
        assert kind in item["param"]


def test_references_and_inputs_mix_in_ref_and_are_recorded(higgs, project, api_key, respx_mock, estimate, media):
    (project / "references" / "hero.jpg").write_bytes(JPEG)
    (project / "references" / "street.mp4").write_bytes(MP4)
    mock_uploads(respx_mock)
    route = mock_submit(respx_mock, "bytedance/seedance-2.5/reference-to-video")

    result = video(
        higgs, "ref", "--model", "seedance-2.5", "--ref", "@hero", "--ref", "inputs/sofa.png", "--video", "@street",
        "--prompt", "Hero sits down", "--no-wait", "--json", "--yes",
    )

    assert result.exit_code == 0, result.output
    assert body(route) == {
        "prompt": "Hero sits down",
        "image_urls": [upload(1), upload(2)],
        "video_urls": [upload(3)],
    }
    record = json.loads(result.output)[0]["record"]
    assert [(item["param"], item["source"]) for item in record["inputs"]] == [
        ("image_urls[0]", "@hero"),
        ("image_urls[1]", "inputs/sofa.png"),
        ("video_urls[0]", "@street"),
    ]


def test_a_prompt_token_without_its_reference_fails_before_any_upload(higgs, project, api_key, respx_mock, media):
    # Cinema Studio 4.0 refers to its references as <<<image_N>>>: two --ref, no third image.
    result = video(
        higgs, "ref", "--model", "cinema-studio-4.0", "--ref", "inputs/hero.jpg", "--ref", "inputs/sofa.png",
        "--prompt", "<<<image_1>>> hands <<<image_3>>> to <<<image_2>>>", "--json", "--yes",
    )

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "validation_failed"
    [problem] = error["problems"]
    assert (problem["param"], problem["constraint"]) == ("prompt", "token")
    assert "<<<image_3>>>" in problem["message"] and "image_urls has 2 items" in problem["message"]
    assert not respx_mock.calls


def test_restyle_takes_its_preset_as_a_parameter(higgs, project, api_key, respx_mock, estimate, media):
    mock_uploads(respx_mock)
    route = mock_submit(respx_mock, "higgsfield/genjutsu/restyle/v1.0")
    preset = "0b8a5e7c-2d4f-4f7e-9a51-3c6d2e1f0a9b"

    result = video(
        higgs, "restyle", "--model", "genjutsu", "--video", "inputs/clip.mp4", "--ref", "inputs/hero.jpg",
        "-p", f"preset_id={preset}", "--no-wait", "--json", "--yes",
    )
    assert result.exit_code == 0, result.output
    assert body(route) == {"video_url": upload(1), "image_urls": [upload(2)], "preset_id": preset}

    # Without one it fails validation before anything is uploaded.
    calls = len(respx_mock.calls)
    result = video(higgs, "restyle", "--model", "genjutsu", "--video", "inputs/clip.mp4", "--json", "--yes")
    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "validation_failed"
    assert [(p["param"], p["constraint"]) for p in error["problems"]] == [("preset_id", "required")]
    assert len(respx_mock.calls) == calls


def test_a_reference_the_endpoint_requires_is_asked_for(higgs, project, api_key, respx_mock, estimate, media):
    # Genjutsu object swap requires the image of the object to put in.
    mock_uploads(respx_mock)
    route = mock_submit(respx_mock, "higgsfield/genjutsu/object-swap/v1.0")

    result = video(
        higgs, "edit", "--model", "genjutsu", "--video", "inputs/clip.mp4", "--prompt", "Swap the chair for it",
        "--no-wait", "--yes", input="inputs/sofa.png\n",
    )

    assert result.exit_code == 0, result.output
    assert "Ref" in result.output
    assert body(route) == {"prompt": "Swap the chair for it", "video_url": upload(1), "image_urls": [upload(2)]}


def local_endpoint(config_dir, mode: str, parameters: dict[str, str]) -> None:
    """A `models.local.toml` Endpoint `acme/<mode>` of Model Acme, with optional parameters of these types."""
    tables = "".join(f'\n[endpoint.parameters.{name}]\ntype = "{kind}"\n' for name, kind in parameters.items())
    (config_dir / "models.local.toml").write_text(
        f'[[endpoint]]\nid = "acme/{mode}"\nmodel = "Acme"\nfamily = "acme"\nmode = "{mode}"\n{tables}'
    )


MOTION = {"prompt": "string", "image_url": "string", "video_url": "string"}


@pytest.mark.parametrize(
    ("mode", "parameters", "args", "flag"),
    [
        ("edit", {"prompt": "string", "video_url": "string"}, (), "--video"),
        ("extend", {"prompt": "string", "video_url": "string"}, (), "--video"),
        ("vref", {"prompt": "string", "video_urls": "array"}, (), "--video"),
        ("restyle", {"prompt": "string", "video_url": "string"}, (), "--video"),
        ("motion", MOTION, ("--image", "inputs/hero.jpg"), "--video"),
        ("motion", MOTION, ("--video", "inputs/clip.mp4"), "--image"),
    ],
)
def test_the_input_a_mode_cannot_do_without_is_required_even_if_the_schema_leaves_it_optional(
    higgs, project, api_key, respx_mock, config_dir, media, mode, parameters, args, flag
):
    local_endpoint(config_dir, mode, parameters)

    result = video(higgs, mode, "--model", "acme", "--prompt", "Snow", *args, "--json", "--yes")

    assert result.exit_code == 1, result.output
    error = json.loads(result.output)["error"]
    assert error["code"] == "missing_param"
    assert error["message"] == f"Missing {flag}"
    assert not respx_mock.calls
