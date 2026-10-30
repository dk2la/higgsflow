"""Seam 1: `higgs gen` validates parameters against the catalog before any HTTP call.

Endpoints are real catalog Endpoints; the expected constraints are read off the
documentation snapshot (catalog/llms-full.txt). No respx route is registered in the
failing cases, so any HTTP call at all would fail the test.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

API = "https://api.higgsfield.ai"
KLING_I2V = "kling-video/v3.0/pro/image-to-video"
REQUEST_ID = "d7e6c0f3-6699-4f6c-bb45-2ad7fd9158ff"
# The submit and upload responses from the docs ("Requests and lifecycle", "File uploads").
SUBMITTED = {
    "status": "queued",
    "request_id": REQUEST_ID,
    "status_url": f"{API}/requests/{REQUEST_ID}/status",
    "cancel_url": f"{API}/requests/{REQUEST_ID}/cancel",
}
STORAGE_URL = "https://storage.example.com/presigned-upload-url"
PUBLIC_URL = "https://cdn.example.com/input/example.jpeg"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 32


def gen(higgs, endpoint: str, *params: str, flags: tuple[str, ...] = ()):
    args = ["gen", endpoint]
    for param in params:
        args += ["-p", param]
    return higgs(*args, "--no-wait", "--json", *flags)


def problems(result) -> list[dict[str, Any]]:
    error = json.loads(result.output)["error"]
    assert error["code"] == "validation_failed"
    return list(error["problems"])


def test_an_out_of_range_parameter_fails_naming_the_parameter_and_constraint(higgs, project, api_key, respx_mock):
    # Kling 3.0 Pro image-to-video: duration is an integer from 3 to 15.
    result = gen(higgs, KLING_I2V, "image_url=https://cdn.example.com/a.jpg", "prompt=a cat", "duration=20")

    assert result.exit_code == 1
    [problem] = problems(result)
    assert problem["param"] == "duration"
    assert problem["constraint"] == "maximum"
    assert "15" in problem["message"]
    assert not respx_mock.calls
    assert not list((project / ".hf" / "requests").iterdir())


def test_every_problem_is_reported_at_once(higgs, project, api_key, respx_mock):
    # SOUL 2.0: prompt is required, batch_size is 1 or 4, seed an integer from 1 to 1000000.
    result = gen(higgs, "higgsfield-ai/soul/v2/standard", "batch_size=2", "seed=fast", "promt=a cat")

    assert result.exit_code == 1
    found = {(p["param"], p["constraint"]) for p in problems(result)}
    assert found == {("prompt", "required"), ("batch_size", "enum"), ("seed", "type"), ("promt", "unknown")}
    messages = {p["param"]: p["message"] for p in problems(result)}
    assert "1, 4" in messages["batch_size"]
    assert "did you mean prompt" in messages["promt"]
    assert not respx_mock.calls


def test_a_parameter_inside_an_array_is_named_by_its_path(higgs, project, api_key, respx_mock):
    # Kling 3.0: 1–6 multi_prompt objects, each prompt at most 512 characters.
    shots = json.dumps([{"prompt": "x" * 513, "duration": 3}] * 7)
    result = gen(higgs, KLING_I2V, "image_url=https://cdn.example.com/a.jpg", "prompt=a cat", f"multi_prompt={shots}")

    found = {(p["param"], p["constraint"]) for p in problems(result)}
    assert ("multi_prompt", "maxItems") in found
    assert ("multi_prompt[0].prompt", "maxLength") in found


def test_conditional_rules_of_the_schema_apply(higgs, project, api_key, respx_mock):
    # Kling O3 image reference: with multi_shots true, multi_prompt is required; otherwise prompt is.
    result = gen(higgs, "kling-video/o3/image-reference", "multi_shots=true")
    assert {(p["param"], p["constraint"]) for p in problems(result)} == {("multi_prompt", "required")}

    result = gen(higgs, "kling-video/o3/image-reference", "multi_shots=false")
    assert {(p["param"], p["constraint"]) for p in problems(result)} == {("prompt", "required")}
    assert not respx_mock.calls


def test_prompt_and_negative_prompt_lengths_are_checked(higgs, project, api_key, respx_mock):
    # PixVerse V6: prompt 1–5000 characters, negative prompt 1–2048.
    result = gen(higgs, "pixverse/v6/text-to-video", "prompt=" + "x" * 5001, "negative_prompt=" + "y" * 2049)

    found = {p["param"]: p for p in problems(result)}
    assert found["prompt"]["constraint"] == "maxLength"
    assert "5000" in found["prompt"]["message"]
    assert found["negative_prompt"]["constraint"] == "maxLength"
    assert "2048" in found["negative_prompt"]["message"]


def test_a_negative_prompt_is_unknown_where_the_endpoint_has_none(higgs, project, api_key, respx_mock):
    result = gen(higgs, "higgsfield-ai/soul/v2/standard", "prompt=a cat", "negative_prompt=dogs")

    assert {(p["param"], p["constraint"]) for p in problems(result)} == {("negative_prompt", "unknown")}


def test_prompt_tokens_must_refer_to_attached_media(higgs, project, api_key, respx_mock):
    # Cinema Studio 4.0: one-based <<<image_N>>>, <<<video_N>>>, <<<audio_N>>> tokens, each
    # referring to an attached item of the same media type.
    result = gen(
        higgs,
        "higgsfield/cinema-studio/4.0",
        "prompt=<<<image_1>>> walks into <<<image_2>>>",
        'image_urls=["https://cdn.example.com/a.jpg"]',
    )

    [problem] = problems(result)
    assert problem["param"] == "prompt"
    assert problem["constraint"] == "token"
    assert "<<<image_2>>>" in problem["message"] and "image_urls" in problem["message"]
    # <<<audio_1>>> has no audio_urls either.
    result = gen(higgs, "higgsfield/cinema-studio/4.0", "prompt=to <<<audio_1>>>")
    assert "<<<audio_1>>>" in problems(result)[0]["message"]


def test_a_malformed_uuid_is_rejected(higgs, project, api_key, respx_mock):
    # SOUL 2.0: custom_reference_id is a UUID or null.
    result = gen(higgs, "higgsfield-ai/soul/v2/standard", "prompt=a cat", "custom_reference_id=not-a-uuid")

    [problem] = problems(result)
    assert problem["param"] == "custom_reference_id"
    assert problem["constraint"] == "format"
    assert "uuid" in problem["message"]


def submitted() -> httpx.Response:
    return httpx.Response(200, json=SUBMITTED)


def test_valid_parameters_are_submitted(higgs, project, api_key, respx_mock):
    route = respx_mock.post(f"{API}/{KLING_I2V}").mock(return_value=submitted())

    result = gen(
        higgs, KLING_I2V, "image_url=https://cdn.example.com/a.jpg", "prompt=a cat", "duration=10", "sound=off",
        "cfg_scale=0.5",
    )

    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content)["duration"] == 10
    assert (project / ".hf" / "requests" / f"{REQUEST_ID}.json").is_file()


def test_a_local_file_satisfies_a_url_parameter_and_is_uploaded(higgs, project, api_key, respx_mock):
    # Ads Studio: image_urls items must match ^https?:// — true once the file is uploaded.
    (project / "inputs" / "a.jpg").write_bytes(JPEG)
    respx_mock.post(f"{API}/files/generate-upload-url").mock(
        return_value=httpx.Response(
            200,
            json={
                "public_url": PUBLIC_URL,
                "upload_url": STORAGE_URL,
                "content_type": "image/jpeg",
                "upload_headers": {"Content-Type": "image/jpeg"},
            },
        )
    )
    respx_mock.put(STORAGE_URL).mock(return_value=httpx.Response(200))
    route = respx_mock.post(f"{API}/higgsfield/ads-studio/v1.0").mock(return_value=submitted())

    result = gen(higgs, "higgsfield/ads-studio/v1.0", "prompt=a sneaker ad", 'image_urls=["inputs/a.jpg"]')

    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content)["image_urls"] == [PUBLIC_URL]


def test_no_validate_submits_parameters_the_catalog_would_reject(higgs, project, api_key, respx_mock):
    route = respx_mock.post(f"{API}/{KLING_I2V}").mock(return_value=submitted())

    result = gen(higgs, KLING_I2V, "duration=20", flags=("--no-validate",))

    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {"duration": 20}


def test_an_unknown_endpoint_fails_with_a_hint_to_skip_or_describe_it(higgs, project, api_key, respx_mock, config_dir):
    result = gen(higgs, "kling-video/v4.0/pro/image-to-video", "prompt=a cat")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "unknown_endpoint"
    assert "--no-validate" in error["hint"]
    assert str(config_dir / "models.local.toml") in error["hint"]
    assert not respx_mock.calls


def test_an_unknown_endpoint_with_no_validate_is_submitted(higgs, project, api_key, respx_mock):
    route = respx_mock.post(f"{API}/kling-video/v4.0/pro/image-to-video").mock(return_value=submitted())

    result = gen(higgs, "kling-video/v4.0/pro/image-to-video", "prompt=a cat", flags=("--no-validate",))

    assert result.exit_code == 0, result.output
    assert route.called


def test_problems_as_text_name_each_parameter(higgs, project, api_key, respx_mock):
    result = higgs("gen", KLING_I2V, "-p", "duration=20", "-p", "sound=loud", "--no-wait")

    assert result.exit_code == 1
    output = result.output
    assert "image_url: is required" in output
    assert "duration: must be at most 15" in output
    assert 'sound: must be one of "on", "off"' in output
    assert "higgs models show kling-3.0" in " ".join(output.split())
    assert "[{" not in output  # the structured list is for --json only
