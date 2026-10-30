"""Seam 1: Models described by hand in `<config dir>/models.local.toml`.

They show up in `higgs models list` / `models show` and `higgs gen` validates against them
like against documented Endpoints.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

LOCAL_ID = "kling-video/v4.0/pro/image-to-video"
KLING_4 = """
[[endpoint]]
id = "kling-video/v4.0/pro/image-to-video"
model = "Kling 4.0"
family = "kling"
mode = "i2v"
price = { usd = "0.07", unit = "second" }

[endpoint.parameters.image_url]
type = "string"
required = true
format = "uri"
description = "The first frame."

[endpoint.parameters.prompt]
type = "string"
required = true
max_length = 2500

[endpoint.parameters.duration]
type = "integer"
default = 5
minimum = 3
maximum = 15

[endpoint.parameters.sound]
type = "string"
enum = ["on", "off"]
default = "on"
"""


def write_local(config_dir: Path, text: str) -> Path:
    path = config_dir / "models.local.toml"
    path.write_text(text)
    return path


def gen(higgs, endpoint: str, *params: str):
    args = ["gen", endpoint]
    for param in params:
        args += ["-p", param]
    return higgs(*args, "--no-wait", "--json")


def submitted() -> httpx.Response:
    # The submit response from the docs ("Requests and lifecycle" → Initial response).
    request_id = "d7e6c0f3-6699-4f6c-bb45-2ad7fd9158ff"
    return httpx.Response(200, json={"status": "queued", "request_id": request_id})


def test_a_local_model_appears_in_models_list(higgs, config_dir):
    write_local(config_dir, KLING_4)

    result = higgs("models", "list", "--family", "kling", "--json")

    assert result.exit_code == 0, result.output
    models = {model["slug"]: model for model in json.loads(result.output)["models"]}
    assert models["kling-4.0"] == {
        "slug": "kling-4.0",
        "name": "Kling 4.0",
        "family": "kling",
        "kind": "video",
        "modes": ["i2v"],
        "tiers": {},
        "price": {"usd": "0.07", "unit": "second", "qualifier": "from"},
    }
    assert "kling-3.0" in models  # the documented Models stay


def test_a_local_model_shows_its_parameters_and_prompt_constraints(higgs, config_dir):
    write_local(config_dir, KLING_4)

    result = higgs("models", "show", "kling-4.0", "--json")

    assert result.exit_code == 0, result.output
    [endpoint] = json.loads(result.output)["endpoints"]
    assert endpoint["id"] == "kling-video/v4.0/pro/image-to-video"
    assert endpoint["mode"] == "i2v"
    params = {p["name"]: p for p in endpoint["parameters"]}
    assert list(params) == ["image_url", "prompt", "duration", "sound"]
    assert params["duration"]["minimum"] == 3 and params["duration"]["maximum"] == 15
    assert params["duration"]["default"] == 5
    assert params["sound"]["enum"] == ["on", "off"]
    assert params["image_url"]["required"] is True
    assert endpoint["prompt"]["required"] is True
    assert endpoint["prompt"]["max_length"] == 2500


def test_models_show_as_text_lists_a_local_model(higgs, config_dir):
    write_local(config_dir, KLING_4)

    result = higgs("models", "show", "Kling 4.0")

    assert result.exit_code == 0, result.output
    assert "kling-video/v4.0/pro/image-to-video" in result.output
    assert "duration" in result.output


def test_gen_validates_against_a_local_model(higgs, config_dir, project, api_key, respx_mock):
    write_local(config_dir, KLING_4)

    result = gen(higgs, LOCAL_ID, "prompt=a cat", "duration=20", "sound=loud", "seed=1")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "validation_failed"
    found = {(p["param"], p["constraint"]) for p in error["problems"]}
    assert found == {
        ("image_url", "required"),
        ("duration", "maximum"),
        ("sound", "enum"),
        ("seed", "unknown"),
    }
    assert not respx_mock.calls


def test_gen_submits_valid_parameters_to_a_local_model(higgs, config_dir, project, api_key, respx_mock):
    write_local(config_dir, KLING_4)
    route = respx_mock.post(f"https://api.higgsfield.ai/{LOCAL_ID}").mock(return_value=submitted())

    result = gen(higgs, LOCAL_ID, "image_url=https://cdn.example.com/a.jpg", "prompt=a cat", "duration=10")

    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {
        "image_url": "https://cdn.example.com/a.jpg",
        "prompt": "a cat",
        "duration": 10,
    }


def test_a_local_entry_with_a_documented_id_replaces_that_endpoint(higgs, config_dir, project, api_key, respx_mock):
    # SOUL 2.0 documents batch_size as 1 or 4; the local description allows 2 as well.
    write_local(
        config_dir,
        """
[[endpoint]]
id = "higgsfield-ai/soul/v2/standard"
model = "Soul V2"
family = "soul"
mode = "t2i"

[endpoint.parameters.prompt]
type = "string"
required = true

[endpoint.parameters.batch_size]
type = "integer"
enum = [1, 2, 4]
""",
    )
    respx_mock.post("https://api.higgsfield.ai/higgsfield-ai/soul/v2/standard").mock(return_value=submitted())

    result = gen(higgs, "higgsfield-ai/soul/v2/standard", "prompt=a cat", "batch_size=2")

    assert result.exit_code == 0, result.output
    shown = json.loads(higgs("models", "show", "soul-v2", "--json").output)
    [endpoint] = [e for e in shown["endpoints"] if e["id"] == "higgsfield-ai/soul/v2/standard"]
    assert [p["name"] for p in endpoint["parameters"]] == ["prompt", "batch_size"]


ENTRY = '[[endpoint]]\nid = "x/v1"\nmodel = "X 1"\nfamily = "x"\nmode = "t2v"\n'


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("[[endpoint]\nid = 1", "line 1"),  # not TOML
        (ENTRY.replace('mode = "t2v"\n', ""), "endpoint[0].mode"),  # a key is missing
        (ENTRY.replace('"t2v"', '"txt2vid"'), "unknown mode 'txt2vid'"),
        (ENTRY + "[endpoint.parameters.duration]\nmaximun = 15\n", "endpoint[0].parameters.duration.maximun"),
        (ENTRY + '[endpoint.parameters.duration]\ntype = "int"\n', "endpoint[0].parameters.duration.type"),
        (ENTRY + ENTRY, "x/v1 is described twice"),
        # Kling 3.0 already has three image-to-video tiers; an untiered one would be ambiguous.
        (
            '[[endpoint]]\nid = "kling-video/v3.0/i2v"\nmodel = "Kling 3.0"\nfamily = "kling"\nmode = "i2v"\n',
            "kling-3.0 mode i2v maps to several endpoints",
        ),
        (
            '[[endpoint]]\nid = "kling-video/v3.0/x"\nmodel = "Kling 3.0"\nfamily = "wan"\nmode = "extend"\n',
            "model kling-3.0 belongs to family kling",
        ),
    ],
)
def test_a_malformed_local_file_fails_naming_the_file_and_problem(higgs, config_dir, text, problem):
    path = write_local(config_dir, text)

    result = higgs("models", "list", "--json")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "invalid_models_local"
    assert str(path) in error["message"]
    assert problem in error["message"]
