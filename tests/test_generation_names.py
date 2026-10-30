"""Generation names, `vNN`, `-N` suffixes and Artifacts (DESIGN.md §5)."""

import json
from datetime import timedelta
from itertools import count
from typing import Any

import httpx

SOUL = "higgsfield-ai/soul/v2/standard"
KLING_I2V = "kling-video/v3.0/pro/image-to-video"

_ids = count(1)


def generate(higgs, respx_mock, *args: str, endpoint: str = SOUL, outputs: dict[str, Any] | None = None, files=None):
    """Run `higgs gen` through submit → completed with `outputs`, serving `files` (url → bytes)."""
    request_id = f"00000000-0000-4000-8000-{next(_ids):012d}"
    outputs = outputs if outputs is not None else {"images": [{"url": f"https://cdn.example.com/{request_id}.png"}]}
    files = files if files is not None else {item["url"]: b"png" for item in outputs.get("images", [])}
    respx_mock.post(f"https://api.higgsfield.ai/{endpoint}").mock(
        return_value=httpx.Response(200, json={"status": "queued", "request_id": request_id})
    )
    respx_mock.get(f"https://api.higgsfield.ai/requests/{request_id}/status").mock(
        return_value=httpx.Response(
            200,
            json={"status": "completed", "request_id": request_id, "expires_at": "2026-11-06T12:00:00Z", **outputs},
        )
    )
    for url, content in files.items():
        respx_mock.get(url).mock(return_value=httpx.Response(200, content=content))
    result = higgs("gen", endpoint, *args, "--no-validate", "--json")
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def names(output: dict[str, Any]) -> list[str]:
    return [generation["path"].rsplit("/", 1)[1] for generation in output["generations"]]


def test_vnn_counts_requests_with_the_same_slug_across_dates_and_models(higgs, project, api_key, respx_mock, clock):
    first = generate(higgs, respx_mock, "-p", "prompt=Waves at golden hour", "--name", "Sunset Beach")
    clock.current += timedelta(days=1, hours=2, minutes=3)
    second = generate(higgs, respx_mock, "-p", "prompt=Drone shot", "--name", "sunset beach", endpoint=KLING_I2V)
    other = generate(higgs, respx_mock, "-p", "prompt=Night city")

    assert names(first) == ["20261030-1200_higgsfield-ai-soul-v2-standard_gen_sunset-beach_v01.png"]
    assert names(second) == ["20261031-1403_kling-video-v3-0-pro_i2v_sunset-beach_v02.png"]
    assert names(other) == ["20261031-1403_higgsfield-ai-soul-v2-standard_gen_night-city_v01.png"]
    assert (project / "generations" / "2026-10-31" / names(second)[0]).is_file()


def test_the_slug_is_fixed_on_the_record_at_submit(higgs, project, api_key, respx_mock):
    output = generate(higgs, respx_mock, "-p", "prompt=Waves at golden hour", "--name", "Sunset Beach")

    record = output["record"]
    assert record["naming"] == {
        "model_slug": "higgsfield-ai-soul-v2-standard",
        "mode": "gen",
        "slug": "sunset-beach",
        "version": 1,
    }
    assert record["files"] == [f"generations/2026-10-30/{names(output)[0]}"]


def test_several_generations_get_numbered_suffixes(higgs, project, api_key, respx_mock):
    # The image output from the docs ("Requests and lifecycle" → Completed output).
    images = {"images": [{"url": "https://cdn.example.com/image-1.jpg"}, {"url": "https://cdn.example.com/image-2.jpg"}]}

    output = generate(higgs, respx_mock, "-p", "prompt=a cat", "-p", "batch_size=2", outputs=images)

    assert names(output) == [
        "20261030-1200_higgsfield-ai-soul-v2-standard_gen_a-cat_v01-1.jpg",
        "20261030-1200_higgsfield-ai-soul-v2-standard_gen_a-cat_v01-2.jpg",
    ]
    day = project / "generations" / "2026-10-30"
    for name, url in zip(names(output), ["https://cdn.example.com/image-1.jpg", "https://cdn.example.com/image-2.jpg"]):
        assert json.loads((day / f"{name}.json").read_text())["url"] == url


def test_audio_listed_in_both_audio_fields_is_one_generation(higgs, project, api_key, respx_mock):
    # The audio output from the docs repeats the same file in `audio` and `audios`.
    audio = {"audio": {"url": "https://cdn.example.com/audio.mp3"}, "audios": [{"url": "https://cdn.example.com/audio.mp3"}]}

    output = generate(
        higgs, respx_mock, "-p", "prompt=rain", outputs=audio, files={"https://cdn.example.com/audio.mp3": b"mp3"}
    )

    assert names(output) == ["20261030-1200_higgsfield-ai-soul-v2-standard_gen_rain_v01.mp3"]


def test_artifacts_are_named_after_their_generation_and_listed_in_its_metadata(higgs, project, api_key, respx_mock):
    outputs = {
        "video": {"url": "https://cdn.example.com/video.mov"},
        "mov": {"url": "https://cdn.example.com/master.mov"},
        "fbx": {"url": "https://cdn.example.com/scene.fbx"},
    }
    files = {item["url"]: item["url"].encode() for item in outputs.values()}

    output = generate(higgs, respx_mock, "-p", "prompt=a dancer", outputs=outputs, files=files, endpoint=KLING_I2V)

    base = "20261030-1200_kling-video-v3-0-pro_i2v_a-dancer_v01"
    day = project / "generations" / "2026-10-30"
    assert names(output) == [f"{base}.mov"]
    # The `mov` Artifact clashes with the video's extension, so its key is appended.
    assert (day / f"{base}-mov.mov").read_bytes() == b"https://cdn.example.com/master.mov"
    assert (day / f"{base}.fbx").read_bytes() == b"https://cdn.example.com/scene.fbx"
    metadata = json.loads((day / f"{base}.mov.json").read_text())
    assert metadata["artifacts"] == [
        {"key": "mov", "file": f"{base}-mov.mov", "url": "https://cdn.example.com/master.mov"},
        {"key": "fbx", "file": f"{base}.fbx", "url": "https://cdn.example.com/scene.fbx"},
    ]
    assert output["generations"][0]["artifacts"] == [str(day / f"{base}-mov.mov"), str(day / f"{base}.fbx")]
    assert sorted(output["record"]["files"]) == sorted(
        f"generations/2026-10-30/{name}" for name in (f"{base}.mov", f"{base}-mov.mov", f"{base}.fbx")
    )


def test_without_a_prompt_or_name_the_slug_is_untitled(higgs, project, api_key, respx_mock):
    output = generate(higgs, respx_mock, "-p", "seed=42")

    assert names(output) == ["20261030-1200_higgsfield-ai-soul-v2-standard_gen_untitled_v01.png"]


def test_a_request_with_artifacts_only_has_them_as_its_generations(higgs, project, api_key, respx_mock):
    outputs = {"ply": {"url": "https://cdn.example.com/scene.ply"}}

    output = generate(
        higgs, respx_mock, "-p", "prompt=a chair", outputs=outputs, files={"https://cdn.example.com/scene.ply": b"ply"}
    )

    assert names(output) == ["20261030-1200_higgsfield-ai-soul-v2-standard_gen_a-chair_v01.ply"]
    day = project / "generations" / "2026-10-30"
    assert json.loads((day / f"{names(output)[0]}.json").read_text())["output_key"] == "ply"


def test_the_slug_keeps_letters_of_any_script(higgs, project, api_key, respx_mock):
    output = generate(higgs, respx_mock, "-p", "prompt=Закат у моря, кинематографично и тепло")

    assert names(output) == ["20261030-1200_higgsfield-ai-soul-v2-standard_gen_закат-у-моря-кинематографично_v01.png"]
