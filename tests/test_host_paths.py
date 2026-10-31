"""Relative paths typed in a host folder outside the projects root (bin/higgs, ADR 0003).

From such a folder `bin/higgs` runs the container in the projects root (the host folder is
not mounted) and passes the host's `$PWD` as `HIGGS_HOST_PWD`. A relative path is relative
to that host folder, so it must fail as outside the projects root, never be resolved
against the projects root.
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

ENDPOINT = "higgsfield-ai/soul/v2/standard"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def desktop(monkeypatch, projects_root: Path, tmp_path: Path, project: Path) -> Path:
    """`cd ~/Desktop` on the host: the container runs in the projects root, the host folder rides along."""
    host = tmp_path / "Desktop"
    host.mkdir()
    monkeypatch.setenv("HIGGS_HOST_PWD", str(host))
    monkeypatch.chdir(projects_root)
    return host


def write(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def error_of(result) -> Any:
    assert result.exit_code == 1, result.output
    return json.loads(result.output)["error"]


def test_a_relative_input_from_outside_the_projects_root_is_rejected_with_the_copy_hint(
    higgs, project, projects_root, desktop, api_key, respx_mock
):
    # A file of the same name in the projects root must not be uploaded instead.
    write(projects_root / "photo.jpg", JPEG)

    result = higgs(
        "gen", ENDPOINT, "-p", "prompt=a cat", "-p", "image_url=photo.jpg",
        "--project", "demo", "--no-validate", "--no-wait", "--json", "--yes",
    )

    error = error_of(result)
    assert error["code"] == "input_outside_projects_root"
    assert "photo.jpg" in error["message"]
    assert "inputs/" in error["hint"]
    assert not respx_mock.calls


def test_a_relative_prompt_file_from_outside_the_projects_root_is_not_read_from_the_projects_root(
    higgs, project, projects_root, desktop, api_key, respx_mock
):
    (projects_root / "prompt.txt").write_text("the wrong prompt")

    result = higgs(
        "image", "t2i", "--model", "soul-v2", "--prompt-file", "prompt.txt",
        "--project", "demo", "--no-wait", "--json", "--yes",
    )

    error = error_of(result)
    assert error["code"] == "input_outside_projects_root"
    assert "prompt.txt" in error["message"]
    assert "prompts/" in error["hint"]
    assert not respx_mock.calls


@pytest.mark.parametrize("value", ["photo.heic", "~/Desktop/clip.mov", "shots/face.HEIC"])
def test_a_file_name_of_any_type_from_outside_the_projects_root_is_rejected(
    higgs, project, desktop, api_key, respx_mock, value
):
    # No file outside the projects root can be seen, so the name alone makes it an Input.
    result = higgs(
        "gen", ENDPOINT, "-p", "prompt=a cat", "-p", f"image_url={value}",
        "--project", "demo", "--no-validate", "--no-wait", "--json", "--yes",
    )

    error = error_of(result)
    assert error["code"] == "input_outside_projects_root"
    assert value in error["message"]
    assert not respx_mock.calls


def test_an_absolute_file_name_outside_the_projects_root_is_rejected(higgs, project, tmp_path, api_key, respx_mock):
    result = higgs(
        "gen", ENDPOINT, "-p", "prompt=a cat", "-p", f"image_url={tmp_path / 'elsewhere' / 'photo.heic'}",
        "--project", "demo", "--no-validate", "--no-wait", "--json", "--yes",
    )

    assert error_of(result)["code"] == "input_outside_projects_root"
    assert not respx_mock.calls


@pytest.mark.parametrize("prompt", ["a logo for acme.com", "version v2.5", "16:9"])
def test_plain_strings_from_outside_the_projects_root_are_still_sent_as_they_are(
    higgs, project, desktop, api_key, respx_mock, estimate, prompt
):
    submit = respx_mock.post(f"https://api.higgsfield.ai/{ENDPOINT}").mock(
        return_value=httpx.Response(200, json={"status": "queued", "request_id": "req-1"})
    )

    result = higgs(
        "gen", ENDPOINT, "-p", f"prompt={prompt}", "--project", "demo", "--no-validate", "--no-wait", "--json", "--yes"
    )

    assert result.exit_code == 0, result.output
    assert json.loads(submit.calls.last.request.content)["prompt"] == prompt


@pytest.mark.parametrize("command", [("pick",), ("ref", "save")])
def test_a_relative_generation_path_from_outside_the_projects_root_is_rejected(
    higgs, project, projects_root, desktop, command
):
    # The same relative path exists under the projects root's own Project folder layout.
    write(projects_root / "generations" / "2026-10-30" / "a.png", PNG)
    extra = ("hero",) if command == ("ref", "save") else ()

    result = higgs(*command, "generations/2026-10-30/a.png", *extra, "--project", "demo", "--json")

    error = error_of(result)
    assert error["code"] == "input_outside_projects_root"
    assert "inputs/" not in error["hint"]


@pytest.mark.parametrize("image", ["face.jpg", "face.heic", "my face.heic", "face"])
def test_soul_train_images_from_outside_the_projects_root_are_rejected(
    higgs, project, projects_root, desktop, api_key, respx_mock, image
):
    write(projects_root / "face.jpg", JPEG)

    result = higgs("soul", "train", "alice", image, "--project", "demo", "--json")

    error = error_of(result)
    assert error["code"] == "input_outside_projects_root"
    assert image in error["message"]
    assert not respx_mock.calls


def test_a_bare_generation_name_is_still_looked_up_from_outside_the_projects_root(higgs, project, desktop):
    generation = write(project / "generations" / "2026-10-30" / "a.png", PNG)
    generation.with_name("a.png.json").write_text("{}")

    result = higgs("pick", "a.png", "--project", "demo", "--json")

    # Found in generations/ (and then rejected only for its unreadable metadata).
    assert error_of(result)["code"] == "invalid_generation_metadata"


def test_relative_paths_typed_inside_the_projects_root_work_as_before(
    higgs, project, monkeypatch, api_key, respx_mock, estimate
):
    # The wrapper runs in the host's folder when it is inside the projects root.
    monkeypatch.setenv("HIGGS_HOST_PWD", str(project / "inputs"))
    monkeypatch.chdir(project / "inputs")
    write(project / "inputs" / "a.jpg", JPEG)
    respx_mock.put("https://storage.example.com/upload").mock(return_value=httpx.Response(200))
    respx_mock.post("https://api.higgsfield.ai/files/generate-upload-url").mock(
        return_value=httpx.Response(
            200,
            json={
                "public_url": "https://cdn.example.com/a.jpeg",
                "upload_url": "https://storage.example.com/upload",
                "content_type": "image/jpeg",
                "upload_headers": {"Content-Type": "image/jpeg"},
            },
        )
    )
    submit = respx_mock.post(f"https://api.higgsfield.ai/{ENDPOINT}").mock(
        return_value=httpx.Response(200, json={"status": "queued", "request_id": "req-1"})
    )

    result = higgs(
        "gen", ENDPOINT, "-p", "prompt=a cat", "-p", "image_url=a.jpg", "--no-validate", "--no-wait", "--json", "--yes"
    )

    assert result.exit_code == 0, result.output
    assert json.loads(submit.calls.last.request.content)["image_url"] == "https://cdn.example.com/a.jpeg"
