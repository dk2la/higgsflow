"""Inputs: local files in `-p` values are uploaded and replaced by their `public_url`."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from typer.testing import Result

ENDPOINT = "higgsfield-ai/soul/v2/standard"
SUBMIT_URL = f"https://api.higgsfield.ai/{ENDPOINT}"
UPLOAD_URL_ENDPOINT = "https://api.higgsfield.ai/files/generate-upload-url"
STORAGE_URL = "https://storage.example.com/presigned-upload-url"
PUBLIC_URL = "https://cdn.example.com/input/example.jpeg"
REQUEST_ID = "d7e6c0f3-6699-4f6c-bb45-2ad7fd9158ff"

SUBMITTED = {
    "status": "queued",
    "request_id": REQUEST_ID,
    "status_url": f"https://api.higgsfield.ai/requests/{REQUEST_ID}/status",
    "cancel_url": f"https://api.higgsfield.ai/requests/{REQUEST_ID}/cancel",
    "expires_after": 604800,
}

# Minimal files that start with each type's magic bytes.
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 32
# Computed independently on the host: `printf '<the JPEG bytes>' | shasum -a 256`.
JPEG_SHA256 = "a9ff9216a5ed206901c1d39f9ce6d0b6644bc608ed2005736998cf57675beb6e"


def upload_url_response(content_type: str = "image/jpeg", public_url: str = PUBLIC_URL) -> httpx.Response:
    # The response from the docs ("File uploads" → Create an upload URL).
    return httpx.Response(
        200,
        json={
            "public_url": public_url,
            "upload_url": STORAGE_URL,
            "content_type": content_type,
            "upload_headers": {"Content-Type": content_type, "x-amz-tagging": "retention=temporary"},
        },
    )


def write(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def gen(higgs, *params: str):
    args = ["gen", ENDPOINT]
    for param in params:
        args += ["-p", param]
    return higgs(*args, "--no-validate", "--no-wait", "--json", "--yes")


def test_a_local_path_is_uploaded_and_replaced_by_its_public_url(higgs, project, api_key, respx_mock, estimate):
    write(project / "inputs" / "a.jpg", JPEG)
    upload_url = respx_mock.post(UPLOAD_URL_ENDPOINT).mock(return_value=upload_url_response())
    storage = respx_mock.put(STORAGE_URL).mock(return_value=httpx.Response(200))
    submit = respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(200, json=SUBMITTED))

    result = gen(higgs, "prompt=a cat", "image_url=inputs/a.jpg")

    assert result.exit_code == 0, result.output
    assert json.loads(upload_url.calls.last.request.content) == {"content_type": "image/jpeg"}
    put = storage.calls.last.request
    assert put.content == JPEG
    assert put.headers["Content-Type"] == "image/jpeg"
    assert put.headers["x-amz-tagging"] == "retention=temporary"
    assert "Authorization" not in put.headers  # never send the key to the storage URL
    assert json.loads(submit.calls.last.request.content) == {"prompt": "a cat", "image_url": PUBLIC_URL}


def mock_upload(respx_mock: respx.MockRouter, content_type: str = "image/jpeg", public_url: str = PUBLIC_URL) -> respx.Route:
    """Mock one successful upload (upload URL + PUT); returns the upload-URL route."""
    respx_mock.put(STORAGE_URL).mock(return_value=httpx.Response(200))
    return respx_mock.post(UPLOAD_URL_ENDPOINT).mock(return_value=upload_url_response(content_type, public_url))


def mock_submit(respx_mock: respx.MockRouter) -> respx.Route:
    return respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(200, json=SUBMITTED))


def sent_params(route: respx.Route) -> Any:
    return json.loads(route.calls.last.request.content)


def read_record(project: Path) -> Any:
    return json.loads((project / ".hf" / "requests" / f"{REQUEST_ID}.json").read_text())


def test_the_request_record_lists_the_inputs_it_used(higgs, project, api_key, respx_mock, estimate):
    write(project / "inputs" / "a.jpg", JPEG)
    mock_upload(respx_mock)
    mock_submit(respx_mock)

    result = gen(higgs, "image_url=inputs/a.jpg")

    assert result.exit_code == 0, result.output
    record = read_record(project)
    assert record["params"] == {"image_url": PUBLIC_URL}
    assert record["inputs"] == [
        {
            "param": "image_url",
            "source": "inputs/a.jpg",
            "path": "inputs/a.jpg",
            "sha256": JPEG_SHA256,
            "content_type": "image/jpeg",
            "public_url": PUBLIC_URL,
        }
    ]
    assert json.loads(result.output)["record"]["inputs"] == record["inputs"]


def read_uploads(project: Path) -> Any:
    return json.loads((project / ".hf" / "uploads.json").read_text())


def test_an_upload_is_cached_by_content_and_reused(higgs, project, api_key, respx_mock, estimate, clock):
    write(project / "inputs" / "a.jpg", JPEG)
    write(project / "inputs" / "copy-of-a.jpg", JPEG)
    upload_url = mock_upload(respx_mock)
    submit = mock_submit(respx_mock)

    first = gen(higgs, "image_url=inputs/a.jpg")
    clock.advance(days=6, hours=22)
    second = gen(higgs, "image_url=inputs/copy-of-a.jpg")

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert upload_url.call_count == 1
    assert sent_params(submit) == {"image_url": PUBLIC_URL}
    assert read_uploads(project) == {
        JPEG_SHA256: {
            "public_url": PUBLIC_URL,
            "content_type": "image/jpeg",
            "uploaded_at": "2026-10-30T12:00:00Z",
            "expires_at": "2026-11-06T12:00:00Z",
        }
    }


NEW_PUBLIC_URL = "https://cdn.example.com/input/example-2.jpeg"


def test_an_expired_upload_is_uploaded_again(higgs, project, api_key, respx_mock, estimate, clock):
    write(project / "inputs" / "a.jpg", JPEG)
    upload_url = respx_mock.post(UPLOAD_URL_ENDPOINT).mock(
        side_effect=[upload_url_response(public_url=PUBLIC_URL), upload_url_response(public_url=NEW_PUBLIC_URL)]
    )
    respx_mock.put(STORAGE_URL).mock(return_value=httpx.Response(200))
    submit = mock_submit(respx_mock)

    gen(higgs, "image_url=inputs/a.jpg")
    clock.advance(days=7)
    result = gen(higgs, "image_url=inputs/a.jpg")

    assert result.exit_code == 0, result.output
    assert upload_url.call_count == 2
    assert sent_params(submit) == {"image_url": NEW_PUBLIC_URL}
    assert read_uploads(project)[JPEG_SHA256] == {
        "public_url": NEW_PUBLIC_URL,
        "content_type": "image/jpeg",
        "uploaded_at": "2026-11-06T12:00:00Z",
        "expires_at": "2026-11-13T12:00:00Z",
    }


def assert_failed_before_any_request(result: Result, project: Path, code: str) -> Any:
    assert result.exit_code == 1, result.output
    error = json.loads(result.output)["error"]
    assert error["code"] == code
    assert not list((project / ".hf" / "requests").iterdir())
    assert not (project / ".hf" / "uploads.json").exists()
    return error


@pytest.mark.parametrize("exists", [True, False], ids=["existing", "not-mounted"])
def test_a_file_outside_the_projects_root_is_rejected_with_a_hint(
    higgs, project, api_key, respx_mock, tmp_path, exists
):
    # Inside the container a host path outside the projects root is not even visible,
    # so a missing file there gets the same explanation.
    outside = tmp_path / "Downloads" / "a.jpg"
    if exists:
        write(outside, JPEG)

    result = gen(higgs, f"image_url={outside}")

    error = assert_failed_before_any_request(result, project, "input_outside_projects_root")
    assert str(outside) in error["message"]
    assert "inputs/" in error["hint"]
    assert not respx_mock.calls


def test_a_symlink_to_a_file_outside_the_projects_root_is_rejected(higgs, project, api_key, respx_mock, tmp_path):
    # The container sees the link but not its target.
    target = write(tmp_path / "Downloads" / "a.jpg", JPEG)
    (project / "inputs" / "a.jpg").symlink_to(target)

    result = gen(higgs, "image_url=inputs/a.jpg")

    assert_failed_before_any_request(result, project, "input_outside_projects_root")
    assert not respx_mock.calls


def test_paths_are_relative_to_the_current_directory(higgs, project, projects_root, api_key, respx_mock, estimate, monkeypatch):
    write(project / "inputs" / "a.jpg", JPEG)
    higgs("init", "other")
    elsewhere = write(projects_root / "other" / "inputs" / "b.png", PNG)
    respx_mock.post(UPLOAD_URL_ENDPOINT).mock(
        side_effect=[upload_url_response("image/jpeg", PUBLIC_URL), upload_url_response("image/png", PNG_PUBLIC_URL)]
    )
    respx_mock.put(STORAGE_URL).mock(return_value=httpx.Response(200))
    mock_submit(respx_mock)
    monkeypatch.chdir(project / "inputs")

    result = gen(higgs, "image_url=a.jpg", f"end_image_url={elsewhere}")

    assert result.exit_code == 0, result.output
    inputs = read_record(project)["inputs"]
    # Inside the Project the path is kept relative to it; another Project's file stays absolute.
    assert [(item["source"], item["path"]) for item in inputs] == [
        ("a.jpg", "inputs/a.jpg"),
        (str(elsewhere), str(elsewhere)),
    ]


def test_a_missing_file_with_a_media_extension_is_an_error_not_a_string(higgs, project, api_key, respx_mock):
    result = gen(higgs, "image_url=inputs/typo.jpg")

    error = assert_failed_before_any_request(result, project, "input_not_found")
    assert "inputs/typo.jpg" in error["message"]
    assert not respx_mock.calls


PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 32
GIF = b"GIF89a\x01\x00\x01\x00" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 32


@pytest.mark.parametrize(
    ("name", "content", "content_type"),
    [
        ("a.jpeg", JPEG, "image/jpeg"),
        ("a.png", PNG, "image/png"),
        ("a.gif", GIF, "image/gif"),
        ("a.webp", WEBP, "image/webp"),
        ("a.wav", WAV, "audio/wav"),
        ("a.mp4", MP4, "video/mp4"),
    ],
    ids=["jpeg", "png", "gif", "webp", "wav", "mp4"],
)
def test_each_allowed_type_is_uploaded_with_its_content_type(
    higgs, project, api_key, respx_mock, estimate, name, content, content_type
):
    write(project / "inputs" / name, content)
    upload_url = mock_upload(respx_mock, content_type)
    mock_submit(respx_mock)

    result = gen(higgs, f"file=inputs/{name}")

    assert result.exit_code == 0, result.output
    assert sent_params(upload_url) == {"content_type": content_type}
    assert read_record(project)["inputs"][0]["content_type"] == content_type


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("notes.txt", b"just some text\n"),
        ("renamed.jpg", b"%PDF-1.7\n" + b"\x00" * 32),
        ("photo.heic", b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 32),
        ("clip.mov", b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00qt  " + b"\x00" * 32),
        ("voice.m4a", b"\x00\x00\x00\x1cftypM4A \x00\x00\x02\x00M4A isommp42" + b"\x00" * 32),
    ],
    ids=["text", "pdf-renamed-to-jpg", "heic", "quicktime", "m4a"],
)
def test_a_disallowed_file_type_fails_before_any_upload(higgs, project, api_key, respx_mock, name, content):
    write(project / "inputs" / "good.jpg", JPEG)
    write(project / "inputs" / name, content)

    result = gen(higgs, "image_url=inputs/good.jpg", f"end_image_url=inputs/{name}")

    error = assert_failed_before_any_request(result, project, "unsupported_input_type")
    assert f"inputs/{name}" in error["message"]
    assert "jpeg" in error["hint"] and "mp4" in error["hint"]
    assert not respx_mock.calls


PNG_PUBLIC_URL = "https://cdn.example.com/input/example.png"


def test_paths_inside_lists_and_objects_are_uploaded_too(higgs, project, api_key, respx_mock, estimate):
    write(project / "inputs" / "a.jpg", JPEG)
    write(project / "inputs" / "b.png", PNG)
    upload_url = respx_mock.post(UPLOAD_URL_ENDPOINT).mock(
        side_effect=[upload_url_response("image/jpeg", PUBLIC_URL), upload_url_response("image/png", PNG_PUBLIC_URL)]
    )
    respx_mock.put(STORAGE_URL).mock(return_value=httpx.Response(200))
    submit = mock_submit(respx_mock)

    result = gen(
        higgs,
        'images=["inputs/a.jpg", "https://cdn.example.com/already-online.jpg", "inputs/b.png"]',
        'frames={"start": "inputs/a.jpg", "label": "first frame"}',
    )

    assert result.exit_code == 0, result.output
    # The same file twice is one upload.
    assert upload_url.call_count == 2
    assert sent_params(submit) == {
        "images": [PUBLIC_URL, "https://cdn.example.com/already-online.jpg", PNG_PUBLIC_URL],
        "frames": {"start": PUBLIC_URL, "label": "first frame"},
    }
    inputs = read_record(project)["inputs"]
    assert [(item["param"], item["source"], item["public_url"]) for item in inputs] == [
        ("images[0]", "inputs/a.jpg", PUBLIC_URL),
        ("images[2]", "inputs/b.png", PNG_PUBLIC_URL),
        ("frames.start", "inputs/a.jpg", PUBLIC_URL),
    ]


def test_strings_that_are_not_files_are_sent_unchanged(higgs, project, api_key, respx_mock, estimate):
    submit = mock_submit(respx_mock)
    long_prompt = "a very long prompt " * 30  # longer than any file name may be

    result = gen(
        higgs,
        f"prompt={long_prompt}",
        "style=black/white",
        "folder=inputs",  # an existing directory, not a file
        "image_url=https://cdn.example.com/a.jpg",
        "multiline=line one\nends with.jpg",
    )

    assert result.exit_code == 0, result.output
    assert sent_params(submit) == {
        "prompt": long_prompt,
        "style": "black/white",
        "folder": "inputs",
        "image_url": "https://cdn.example.com/a.jpg",
        "multiline": "line one\nends with.jpg",
    }
    assert read_record(project)["inputs"] == []


def test_a_failed_upload_submits_nothing_and_caches_nothing(higgs, project, api_key, respx_mock):
    write(project / "inputs" / "a.jpg", JPEG)
    respx_mock.post(UPLOAD_URL_ENDPOINT).mock(return_value=upload_url_response())
    # S3-style error body for an expired presigned URL.
    respx_mock.put(STORAGE_URL).mock(
        return_value=httpx.Response(403, text="<Error><Code>AccessDenied</Code><Message>Request has expired</Message></Error>")
    )
    submit = mock_submit(respx_mock)

    result = gen(higgs, "image_url=inputs/a.jpg")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "upload_failed"
    assert "a.jpg" in error["message"] and "403" in error["message"]
    assert not submit.called
    assert not list((project / ".hf" / "requests").iterdir())
    assert not (project / ".hf" / "uploads.json").exists()
