"""`higgs pick <generation>`: copying a Generation with its Artifacts and `.json` into `final/`."""

import json
from pathlib import Path
from typing import Any

import httpx

ENDPOINT = "higgsfield-ai/soul/v2/standard"
REQUEST_ID = "d7e6c0f3-6699-4f6c-bb45-2ad7fd9158ff"
# A video with two Artifacts, one clashing with the video's extension (DESIGN.md §5).
OUTPUTS = {
    "video": {"url": "https://cdn.example.com/video.mov"},
    "mov": {"url": "https://cdn.example.com/master.mov"},
    "fbx": {"url": "https://cdn.example.com/scene.fbx"},
}


def generate(higgs, respx_mock, outputs: dict[str, Any] = OUTPUTS) -> Path:
    """Run `higgs gen` through submit → completed, serving each output URL's own text as its bytes.

    Returns the downloaded Generation (the first one).
    """
    respx_mock.post(f"https://api.higgsfield.ai/{ENDPOINT}").mock(
        return_value=httpx.Response(200, json={"status": "queued", "request_id": REQUEST_ID})
    )
    respx_mock.get(f"https://api.higgsfield.ai/requests/{REQUEST_ID}/status").mock(
        return_value=httpx.Response(200, json={"status": "completed", "request_id": REQUEST_ID, **outputs})
    )
    for item in outputs.values():
        respx_mock.get(item["url"]).mock(return_value=httpx.Response(200, content=item["url"].encode()))
    result = higgs("gen", ENDPOINT, "-p", "prompt=A dancer", "--no-validate", "--json")
    assert result.exit_code == 0, result.output
    return Path(json.loads(result.output)["generations"][0]["path"])


def test_pick_copies_the_generation_its_artifacts_and_metadata_into_final(higgs, project: Path, api_key, respx_mock):
    generation = generate(higgs, respx_mock)
    name = generation.name  # e.g. `..._a-dancer_v01.mov`
    stem = name.removesuffix(".mov")

    result = higgs("pick", str(generation.relative_to(project)), "--json")

    assert result.exit_code == 0, result.output
    final = project / "final"
    assert sorted(path.name for path in final.iterdir()) == sorted(
        [name, f"{name}.json", f"{stem}-mov.mov", f"{stem}.fbx"]
    )
    assert (final / name).read_bytes() == b"https://cdn.example.com/video.mov"
    assert (final / f"{stem}-mov.mov").read_bytes() == b"https://cdn.example.com/master.mov"
    assert (final / f"{stem}.fbx").read_bytes() == b"https://cdn.example.com/scene.fbx"
    assert (final / f"{name}.json").read_text() == generation.with_name(f"{name}.json").read_text()
    assert json.loads(result.output) == {
        "source": str(generation),
        "generation": str(final / name),
        "metadata": str(final / f"{name}.json"),
        "artifacts": [str(final / f"{stem}-mov.mov"), str(final / f"{stem}.fbx")],
        "already_picked": False,
    }


def test_picked_files_are_independent_copies(higgs, project: Path, api_key, respx_mock):
    generation = generate(higgs, respx_mock)
    artifact = generation.with_name(generation.name.removesuffix(".mov") + ".fbx")
    higgs("pick", str(generation), "--json")

    generation.write_bytes(b"edited")
    artifact.write_bytes(b"edited")
    generation.with_name(f"{generation.name}.json").write_text("{}")

    final = project / "final"
    for name in (generation.name, f"{generation.name}.json", artifact.name):
        assert not (final / name).is_symlink()
        assert (final / name).stat().st_ino != generation.with_name(name).stat().st_ino
    assert (final / generation.name).read_bytes() == b"https://cdn.example.com/video.mov"
    assert (final / artifact.name).read_bytes() == b"https://cdn.example.com/scene.fbx"
    assert json.loads((final / f"{generation.name}.json").read_text())["file"] == generation.name


def test_picking_an_unknown_generation_fails_clearly(higgs, project: Path):
    result = higgs("pick", "generations/2026-10-30/missing.png", "--json")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "generation_not_found"
    assert "missing.png" in error["message"]
    assert list((project / "final").iterdir()) == []


def test_a_bare_file_name_is_looked_up_under_generations(higgs, project: Path, api_key, respx_mock, monkeypatch):
    generation = generate(higgs, respx_mock)
    monkeypatch.chdir(project / "prompts")  # anywhere in the Project

    result = higgs("pick", generation.name, "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["source"] == str(generation)
    assert (project / "final" / generation.name).is_file()


def test_a_file_without_metadata_is_not_picked(higgs, project: Path):
    stray = project / "generations" / "2026-10-30" / "stray.png"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"png")

    result = higgs("pick", str(stray), "--json")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "generation_metadata_missing"
    assert "stray.png.json" in error["message"]
    assert list((project / "final").iterdir()) == []


def test_a_generation_whose_artifact_is_gone_is_not_picked(higgs, project: Path, api_key, respx_mock):
    generation = generate(higgs, respx_mock)
    artifact = generation.with_name(generation.name.removesuffix(".mov") + ".fbx")
    artifact.unlink()

    result = higgs("pick", str(generation), "--json")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "artifact_missing"
    assert artifact.name in error["message"]
    assert list((project / "final").iterdir()) == []


def test_picking_again_refreshes_the_copy_and_says_it_was_already_picked(higgs, project: Path, api_key, respx_mock):
    generation = generate(higgs, respx_mock)
    higgs("pick", str(generation), "--json")
    (project / "final" / generation.name).write_bytes(b"damaged")

    result = higgs("pick", str(generation), "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["already_picked"] is True
    assert (project / "final" / generation.name).read_bytes() == b"https://cdn.example.com/video.mov"
    assert len(list((project / "final").iterdir())) == 4


def test_only_files_under_the_projects_generations_can_be_picked(higgs, project: Path, api_key, respx_mock):
    generation = generate(higgs, respx_mock)
    higgs("pick", str(generation), "--json")
    picked = project / "final" / generation.name

    result = higgs("pick", str(picked), "--json")

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "not_a_generation"
    assert picked.read_bytes() == b"https://cdn.example.com/video.mov"


def test_pick_reports_what_it_copied(higgs, project: Path, api_key, respx_mock):
    generation = generate(higgs, respx_mock)

    result = higgs("pick", str(generation))

    assert result.exit_code == 0, result.output
    assert f"Picked {generation.name} with 2 Artifact(s) into {project / 'final'}" in result.output


def test_a_generation_with_unreadable_metadata_is_not_picked(higgs, project: Path, api_key, respx_mock):
    generation = generate(higgs, respx_mock)
    generation.with_name(f"{generation.name}.json").write_text("not json")

    result = higgs("pick", str(generation), "--json")

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "invalid_generation_metadata"
    assert list((project / "final").iterdir()) == []
