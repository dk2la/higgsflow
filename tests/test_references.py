"""References: `higgs ref save` and `@name` in parameter values (DESIGN.md §5)."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

ENDPOINT = "higgsfield-ai/soul/v2/standard"
SUBMIT_URL = f"https://api.higgsfield.ai/{ENDPOINT}"
REQUEST_ID = "d7e6c0f3-6699-4f6c-bb45-2ad7fd9158ff"
STATUS_URL = f"https://api.higgsfield.ai/requests/{REQUEST_ID}/status"
UPLOAD_URL_ENDPOINT = "https://api.higgsfield.ai/files/generate-upload-url"
STORAGE_URL = "https://storage.example.com/presigned-upload-url"
PUBLIC_URL = "https://cdn.example.com/input/example.png"

# Response bodies from the docs ("Requests and lifecycle").
SUBMITTED = {
    "status": "queued",
    "request_id": REQUEST_ID,
    "status_url": STATUS_URL,
    "cancel_url": f"https://api.higgsfield.ai/requests/{REQUEST_ID}/cancel",
    "expires_after": 604800,
}
COMPLETED = {
    "status": "completed",
    "request_id": REQUEST_ID,
    "images": [{"url": "https://cdn.example.com/image-1.png"}],
}

# Minimal files that start with each type's magic bytes.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32

GENERATION = "generations/2026-10-30/20261030-1200_soul-v2_t2i_a-red-fox_v01.png"


def make_generation(higgs, respx_mock: respx.MockRouter) -> str:
    """Run a real `higgs gen` that downloads one PNG Generation; returns its path in the Project."""
    respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(200, json=SUBMITTED))
    respx_mock.get(STATUS_URL).mock(return_value=httpx.Response(200, json=COMPLETED))
    respx_mock.get("https://cdn.example.com/image-1.png").mock(return_value=httpx.Response(200, content=PNG))
    result = higgs("gen", ENDPOINT, "-p", "prompt=a red fox", "--no-validate", "--json", "--yes")
    assert result.exit_code == 0, result.output
    return GENERATION


def test_ref_save_copies_the_generation_and_its_metadata_into_references(higgs, project, api_key, respx_mock, estimate):
    generation = make_generation(higgs, respx_mock)

    result = higgs("ref", "save", generation, "hero", "--json")

    assert result.exit_code == 0, result.output
    assert (project / "references" / "hero.png").read_bytes() == PNG
    metadata = json.loads((project / "references" / "hero.png.json").read_text())
    assert metadata == json.loads((project / f"{generation}.json").read_text())
    assert metadata["request_id"] == REQUEST_ID
    assert (project / generation).is_file()  # copied, not moved
    output = json.loads(result.output)
    assert output["name"] == "hero"
    assert output["path"] == str(project / "references" / "hero.png")


def write(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def error(result) -> Any:
    return json.loads(result.output)["error"]


def test_ref_save_to_a_name_taken_with_another_extension_fails(higgs, project, api_key, respx_mock, estimate):
    generation = make_generation(higgs, respx_mock)
    write(project / "references" / "hero.mp4", MP4)

    result = higgs("ref", "save", generation, "hero", "--json")

    assert result.exit_code == 1
    assert error(result)["code"] == "reference_exists"
    assert "references/hero.mp4" in error(result)["message"]
    assert "--force" in error(result)["hint"]
    assert (project / "references" / "hero.mp4").read_bytes() == MP4
    assert not (project / "references" / "hero.png").exists()


def test_ref_save_with_force_replaces_the_old_reference_and_its_metadata(higgs, project, api_key, respx_mock, estimate):
    generation = make_generation(higgs, respx_mock)
    write(project / "references" / "hero.mp4", MP4)
    write(project / "references" / "hero.mp4.json", b"{}")
    write(project / "references" / "heroine.png", PNG)

    result = higgs("ref", "save", generation, "hero", "--force", "--json")

    assert result.exit_code == 0, result.output
    assert sorted(path.name for path in (project / "references").iterdir()) == [
        "hero.png",
        "hero.png.json",
        "heroine.png",
    ]
    assert (project / "references" / "hero.png").read_bytes() == PNG
    assert json.loads(result.output)["replaced"] == [str(project / "references" / "hero.mp4")]


def test_ref_save_of_an_unknown_generation_fails(higgs, project):
    result = higgs("ref", "save", "generations/2026-10-30/nope.png", "hero", "--json")

    assert result.exit_code == 1
    assert error(result)["code"] == "generation_not_found"
    assert "generations/2026-10-30/nope.png" in error(result)["message"]
    assert not any((project / "references").iterdir())


def test_ref_save_of_a_metadata_file_points_to_its_generation(higgs, project, api_key, respx_mock, estimate):
    generation = make_generation(higgs, respx_mock)

    result = higgs("ref", "save", f"{generation}.json", "hero", "--json")

    assert result.exit_code == 1
    assert error(result)["code"] == "not_a_generation"
    assert Path(generation).name in error(result)["hint"]
    assert not any((project / "references").iterdir())


def test_ref_save_finds_a_generation_by_its_bare_file_name(higgs, project, api_key, respx_mock, estimate, monkeypatch):
    generation = make_generation(higgs, respx_mock)
    monkeypatch.chdir(project / "prompts")

    result = higgs("ref", "save", Path(generation).name, "hero", "--json")

    assert result.exit_code == 0, result.output
    assert (project / "references" / "hero.png").read_bytes() == PNG
    assert (project / "references" / "hero.png.json").is_file()


@pytest.mark.parametrize("name", ["chars/hero", "@hero", ".hero", "", "my hero"])
def test_ref_save_rejects_a_name_that_cannot_be_used_as_at_name(higgs, project, name):
    write(project / "inputs" / "a.png", PNG)

    result = higgs("ref", "save", "inputs/a.png", name, "--json")

    assert result.exit_code == 1
    assert error(result)["code"] == "invalid_reference_name"
    assert not any((project / "references").iterdir())


def mock_upload(respx_mock: respx.MockRouter) -> respx.Route:
    """Mock one successful PNG upload (upload URL + PUT); returns the PUT route."""
    respx_mock.post(UPLOAD_URL_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "public_url": PUBLIC_URL,
                "upload_url": STORAGE_URL,
                "content_type": "image/png",
                "upload_headers": {"Content-Type": "image/png"},
            },
        )
    )
    return respx_mock.put(STORAGE_URL).mock(return_value=httpx.Response(200))


def submit_no_wait(higgs, respx_mock: respx.MockRouter, *params: str):
    """`higgs gen ... --no-wait --json` with the given `-p` values; returns (result, submit route)."""
    submit = respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(200, json=SUBMITTED))
    args = ["gen", ENDPOINT]
    for param in params:
        args += ["-p", param]
    return higgs(*args, "--no-validate", "--no-wait", "--json", "--yes"), submit


def read_record(project: Path) -> Any:
    return json.loads((project / ".hf" / "requests" / f"{REQUEST_ID}.json").read_text())


def test_at_name_resolves_to_a_reference_put_by_hand_and_is_uploaded(higgs, project, api_key, respx_mock, estimate):
    write(project / "references" / "hero.png", PNG)
    write(project / "references" / "hero.png.json", b"{}")  # metadata, not a second Reference
    storage = mock_upload(respx_mock)

    result, submit = submit_no_wait(higgs, respx_mock, "prompt=a red fox", 'images=[{"image_url": "@hero"}]')

    assert result.exit_code == 0, result.output
    assert storage.calls.last.request.content == PNG
    assert json.loads(submit.calls.last.request.content) == {"prompt": "a red fox", "images": [{"image_url": PUBLIC_URL}]}
    [reference] = read_record(project)["inputs"]
    assert reference["param"] == "images[0].image_url"
    assert reference["source"] == "@hero"
    assert reference["path"] == "references/hero.png"
    assert reference["public_url"] == PUBLIC_URL


def test_a_saved_generation_is_used_as_at_name_in_the_next_request(higgs, project, api_key, respx_mock, estimate):
    generation = make_generation(higgs, respx_mock)
    assert higgs("ref", "save", generation, "fox").exit_code == 0
    storage = mock_upload(respx_mock)

    result = higgs("gen", ENDPOINT, "-p", "prompt=the fox at night", "-p", "image_url=@fox", "--no-validate", "--no-wait", "--json", "--yes")

    assert result.exit_code == 0, result.output
    assert storage.calls.last.request.content == PNG
    assert json.loads(respx_mock.calls.last.request.content) == {"prompt": "the fox at night", "image_url": PUBLIC_URL}
    [reference] = json.loads(result.output)["record"]["inputs"]
    assert (reference["source"], reference["path"]) == ("@fox", "references/fox.png")


def test_at_name_satisfies_a_url_parameter_when_validated_against_the_catalog(higgs, project, api_key, respx_mock, estimate):
    # Ads Studio: image_urls items must match ^https?:// — true once the Reference is uploaded.
    write(project / "references" / "hero.png", PNG)
    mock_upload(respx_mock)
    submit = respx_mock.post("https://api.higgsfield.ai/higgsfield/ads-studio/v1.0").mock(
        return_value=httpx.Response(200, json=SUBMITTED)
    )

    result = higgs(
        "gen", "higgsfield/ads-studio/v1.0", "-p", "prompt=a sneaker ad", "-p", 'image_urls=["@hero"]', "--no-wait", "--json", "--yes"
    )

    assert result.exit_code == 0, result.output
    assert json.loads(submit.calls.last.request.content)["image_urls"] == [PUBLIC_URL]


def test_an_ambiguous_at_name_fails_listing_the_matches_and_submits_nothing(higgs, project, api_key, respx_mock):
    write(project / "references" / "hero.png", PNG)
    write(project / "references" / "hero.mp4", MP4)  # put by hand; `ref save` never allows this

    result, submit = submit_no_wait(higgs, respx_mock, "image_url=@hero")

    assert result.exit_code == 1
    assert error(result)["code"] == "ambiguous_reference"
    assert "references/hero.mp4" in error(result)["message"]
    assert "references/hero.png" in error(result)["message"]
    assert not submit.called
    assert len(respx_mock.calls) == 0  # nothing uploaded either


def test_an_unknown_at_name_fails_listing_the_references_close_matches_first(higgs, project, api_key, respx_mock):
    for name in ("alley.png", "heroine.png", "hero-2.mp4", "sunset.png"):
        write(project / "references" / name, PNG)

    result, submit = submit_no_wait(higgs, respx_mock, "image_url=@hero")

    assert result.exit_code == 1
    assert error(result)["code"] == "reference_not_found"
    assert "@hero" in error(result)["message"]
    hint = error(result)["hint"]
    assert hint.index("@heroine") < hint.index("@alley")
    assert hint.index("@hero-2") < hint.index("@alley")
    assert "@sunset" in hint
    assert not submit.called


def test_an_unknown_at_name_in_a_project_without_references_says_how_to_add_one(higgs, project, api_key, respx_mock):
    result, submit = submit_no_wait(higgs, respx_mock, "image_url=@hero")

    assert result.exit_code == 1
    assert error(result)["code"] == "reference_not_found"
    assert "higgs ref save" in error(result)["hint"]
    assert not submit.called


def test_a_double_at_sends_a_literal_at_and_text_with_spaces_is_never_a_reference(higgs, project, api_key, respx_mock, estimate):
    result, submit = submit_no_wait(
        higgs, respx_mock, "prompt=@hero walks into the bar", "style=@@hero", 'tags=["@@a", "@@@b"]'
    )

    assert result.exit_code == 0, result.output
    assert json.loads(submit.calls.last.request.content) == {
        "prompt": "@hero walks into the bar",
        "style": "@hero",
        "tags": ["@a", "@@b"],
    }
    assert read_record(project)["inputs"] == []


def test_ref_list_shows_each_reference_with_its_type_size_and_source(higgs, project, api_key, respx_mock, estimate):
    generation = make_generation(higgs, respx_mock)
    assert higgs("ref", "save", generation, "fox").exit_code == 0
    write(project / "references" / "alley.mp4", MP4)  # put by hand: no metadata

    result = higgs("ref", "list", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "references": [
            {
                "name": "alley",
                "path": str(project / "references" / "alley.mp4"),
                "content_type": "video/mp4",
                "size": len(MP4),
                "generation": None,
                "endpoint": None,
            },
            {
                "name": "fox",
                "path": str(project / "references" / "fox.png"),
                "content_type": "image/png",
                "size": len(PNG),
                "generation": Path(generation).name,
                "endpoint": ENDPOINT,
            },
        ]
    }


def test_ref_save_and_list_as_text(higgs, project, api_key, respx_mock, estimate):
    generation = make_generation(higgs, respx_mock)
    write(project / "references" / "hero.mp4", MP4)

    saved = higgs("ref", "save", generation, "hero", "--force")
    listed = higgs("ref", "list")

    assert saved.exit_code == 0, saved.output
    assert "Replaced references/hero.mp4" in saved.output
    assert "Saved Reference @hero: references/hero.png" in saved.output
    assert listed.exit_code == 0, listed.output
    assert "@hero" in listed.output
    assert "hero.png" in listed.output


def test_ref_save_of_a_file_outside_generations_fails_and_leaves_references_untouched(higgs, project):
    # A plain file is not a Generation; it becomes a Reference only by copying it into references/ by hand.
    write(project / "inputs" / "a.png", PNG)

    result = higgs("ref", "save", "inputs/a.png", "hero", "--json")

    assert result.exit_code == 1
    assert error(result)["code"] == "not_a_generation"
    assert not any((project / "references").iterdir())


def test_ref_save_of_a_file_without_metadata_fails(higgs, project):
    write(project / "generations" / "2026-10-30" / "stray.png", PNG)

    result = higgs("ref", "save", "generations/2026-10-30/stray.png", "hero", "--json")

    assert result.exit_code == 1
    assert error(result)["code"] == "generation_metadata_missing"
    assert "stray.png.json" in error(result)["message"]
    assert not any((project / "references").iterdir())


# A video with two Artifacts, one clashing with the video's extension (as in test_pick).
WITH_ARTIFACTS = {
    "video": {"url": "https://cdn.example.com/video.mov"},
    "mov": {"url": "https://cdn.example.com/master.mov"},
    "fbx": {"url": "https://cdn.example.com/scene.fbx"},
}


def make_generation_with_artifacts(higgs, respx_mock: respx.MockRouter) -> Path:
    """Run a real `higgs gen` whose Generation has two Artifacts; returns the Generation's path."""
    respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(200, json=SUBMITTED))
    respx_mock.get(STATUS_URL).mock(
        return_value=httpx.Response(200, json={"status": "completed", "request_id": REQUEST_ID, **WITH_ARTIFACTS})
    )
    for item in WITH_ARTIFACTS.values():
        respx_mock.get(item["url"]).mock(return_value=httpx.Response(200, content=item["url"].encode()))
    result = higgs("gen", ENDPOINT, "-p", "prompt=a red fox", "--no-validate", "--json", "--yes")
    assert result.exit_code == 0, result.output
    return Path(json.loads(result.output)["generations"][0]["path"])


def test_ref_save_copies_the_artifacts_under_the_reference_name(higgs, project, api_key, respx_mock, estimate):
    generation = make_generation_with_artifacts(higgs, respx_mock)

    result = higgs("ref", "save", str(generation), "hero", "--json")

    assert result.exit_code == 0, result.output
    references = project / "references"
    assert sorted(path.name for path in references.iterdir()) == ["hero-mov.mov", "hero.fbx", "hero.mov", "hero.mov.json"]
    assert (references / "hero.mov").read_bytes() == b"https://cdn.example.com/video.mov"
    assert (references / "hero-mov.mov").read_bytes() == b"https://cdn.example.com/master.mov"
    assert (references / "hero.fbx").read_bytes() == b"https://cdn.example.com/scene.fbx"
    # The copied metadata lists the Artifacts that are actually next to it.
    metadata = json.loads((references / "hero.mov.json").read_text())
    assert [entry["file"] for entry in metadata["artifacts"]] == ["hero-mov.mov", "hero.fbx"]
    assert metadata["file"] == generation.name  # still names the Generation it came from
    assert json.loads(result.output)["artifacts"] == [str(references / "hero-mov.mov"), str(references / "hero.fbx")]
    # Artifacts travel with their Reference; they are not References of their own.
    listed = json.loads(higgs("ref", "list", "--json").output)["references"]
    assert [item["name"] for item in listed] == ["hero"]


def test_ref_save_with_force_also_removes_the_old_references_artifacts(higgs, project, api_key, respx_mock, estimate):
    generation = make_generation_with_artifacts(higgs, respx_mock)
    assert higgs("ref", "save", str(generation), "hero", "--json").exit_code == 0
    assert (project / "references" / "hero.fbx").is_file()
    # A second Generation, without Artifacts, made from the first one's metadata.
    write(project / "generations" / "2026-10-30" / "still.png", PNG)
    plain = json.loads(generation.with_name(generation.name + ".json").read_text())
    plain.update(file="still.png", artifacts=[])
    (project / "generations" / "2026-10-30" / "still.png.json").write_text(json.dumps(plain))

    result = higgs("ref", "save", "generations/2026-10-30/still.png", "hero", "--force", "--json")

    assert result.exit_code == 0, result.output
    assert sorted(path.name for path in (project / "references").iterdir()) == ["hero.png", "hero.png.json"]
