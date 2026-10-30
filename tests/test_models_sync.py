"""Seam 1: `higgs models sync`, with the docs and pricing downloads served by respx.

The docs are the committed snapshot (catalog/llms-full.txt), optionally with an Endpoint's
page cut out, so a sync visibly changes the catalog; the pricing data is catalog/pricing.json.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

SNAPSHOT = Path(__file__).resolve().parent.parent / "catalog"
DOCS_URL = "https://docs.higgsfield.ai/docs/llms-full.txt"
PRICING_URL = "https://dash.higgsfield.ai/api/v2/pricing/models/"
MODELS_URL = "https://api.higgsfield.ai/models"

DOCS = (SNAPSHOT / "llms-full.txt").read_text(encoding="utf-8")
PRICING = json.loads((SNAPSHOT / "pricing.json").read_text(encoding="utf-8"))


def docs_without(*endpoint_ids: str) -> str:
    """The docs snapshot with the pages of these Endpoints removed."""
    pages = re.split(r"\n(?=# .+\nSource: )", DOCS)
    kept = [page for page in pages if not any(f"**Endpoint ID:** `{e}`" in page for e in endpoint_ids)]
    assert len(kept) == len(pages) - len(endpoint_ids)
    return "\n".join(kept)


@pytest.fixture
def serve(respx_mock):
    """Serve the given docs text and the pricing snapshot."""

    def serve_docs(docs: str = DOCS) -> None:
        respx_mock.get(DOCS_URL).respond(200, text=docs)
        respx_mock.get(PRICING_URL).respond(200, json=PRICING)

    return serve_docs


def sync(higgs) -> dict[str, Any]:
    result = higgs("models", "sync", "--json")
    assert result.exit_code == 0, result.output
    synced: dict[str, Any] = json.loads(result.stdout)
    return synced


def listed(higgs) -> dict[str, dict[str, Any]]:
    """`higgs models list --json`, keyed by Model slug."""
    return {model["slug"]: model for model in json.loads(higgs("models", "list", "--json").stdout)["models"]}


def model_slugs(higgs) -> list[str]:
    return list(listed(higgs))


def test_sync_writes_the_user_catalog_which_then_replaces_the_baked_in_one(higgs, serve, config_dir):
    serve(docs_without("z-image/turbo"))

    synced = sync(higgs)

    assert synced["path"] == str(config_dir / "catalog.json")
    assert (config_dir / "catalog.json").is_file()
    assert "z-image-turbo" not in model_slugs(higgs)
    assert "kling-3.0" in model_slugs(higgs)


def test_sync_reports_endpoints_removed_from_and_added_to_the_previous_catalog(higgs, serve):
    serve(docs_without("z-image/turbo", "ideogram/v4.0"))
    first = sync(higgs)

    serve(docs_without("z-image/turbo"))
    second = sync(higgs)

    # The first sync compares with the baked-in catalog, the second with the first's result.
    assert (first["added"], first["removed"]) == ([], ["ideogram/v4.0", "z-image/turbo"])
    assert (second["added"], second["removed"]) == (["ideogram/v4.0"], [])


def test_sync_prints_what_changed(higgs, serve):
    serve(docs_without("z-image/turbo"))

    result = higgs("models", "sync")

    assert result.exit_code == 0
    assert "Removed 1 endpoint" in result.output
    assert "z-image/turbo" in result.output


@pytest.mark.parametrize(
    "docs_response",
    [
        httpx.Response(500, text="Internal Server Error"),
        httpx.ConnectError("Name or service not known"),
        # A page that is not the docs dump, e.g. a captive portal or an error page.
        httpx.Response(200, text="<html><body>Not here</body></html>"),
    ],
    ids=["http-error", "network-error", "not-the-docs"],
)
def test_a_failed_docs_download_fails_the_sync_and_keeps_the_previous_user_catalog(
    higgs, serve, respx_mock, config_dir, docs_response
):
    serve(docs_without("z-image/turbo"))
    sync(higgs)
    before = (config_dir / "catalog.json").read_bytes()

    if isinstance(docs_response, Exception):
        respx_mock.get(DOCS_URL).mock(side_effect=docs_response)
    else:
        respx_mock.get(DOCS_URL).mock(return_value=docs_response)
    result = higgs("models", "sync", "--json")

    assert result.exit_code == 1
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "catalog_sync_failed"
    assert "docs" in error["message"]
    assert (config_dir / "catalog.json").read_bytes() == before
    assert "z-image-turbo" not in model_slugs(higgs)


def test_sync_follows_the_pricing_pages(higgs, respx_mock):
    # Z-Image Turbo's price is only on the second page.
    def on_page_two(model: dict[str, Any]) -> bool:
        return any(mode["slug"] == "z-image/turbo" for mode in model["modes"])

    page_two = [model for model in PRICING["results"] if on_page_two(model)]
    page_one = [model for model in PRICING["results"] if not on_page_two(model)]
    respx_mock.get(DOCS_URL).respond(200, text=DOCS)
    respx_mock.get(PRICING_URL, params={"page": "2"}).respond(
        200, json={"count": len(PRICING["results"]), "next": None, "results": page_two}
    )
    respx_mock.get(PRICING_URL, params={"page": "1"}).respond(
        200,
        json={"count": len(PRICING["results"]), "next": f"{PRICING_URL}?page=2&page_size=50", "results": page_one},
    )

    sync(higgs)

    models = listed(higgs)
    assert models["z-image-turbo"]["price"] == {"usd": "0.015", "unit": "image", "qualifier": "exact"}
    assert models["kling-3.0"]["price"] == {"usd": "0.042", "unit": "second", "qualifier": "from"}


@pytest.mark.parametrize(
    "pricing_response",
    [httpx.Response(502, text="Bad Gateway"), httpx.Response(200, text="<html>maintenance</html>")],
    ids=["http-error", "not-json"],
)
def test_without_pricing_data_the_sync_keeps_the_previous_prices_and_says_so(higgs, respx_mock, pricing_response):
    respx_mock.get(DOCS_URL).respond(200, text=DOCS)
    respx_mock.get(PRICING_URL).mock(return_value=pricing_response)

    synced = sync(higgs)

    assert any("pricing" in note for note in synced["notes"])
    models = listed(higgs)
    assert models["kling-3.0"]["price"] == {"usd": "0.042", "unit": "second", "qualifier": "from"}


@pytest.mark.parametrize(
    "models_response",
    [
        httpx.Response(500, json={"detail": "Internal error"}),
        httpx.Response(404, json={"detail": "Not Found"}),
        httpx.ConnectError("Connection refused"),
        httpx.Response(200, text="<html>hello</html>"),
        httpx.Response(200, json={"status": "ok", "count": 3}),
        httpx.Response(200, json=["kling", "seedance", 42, None]),
        httpx.Response(200, json=[{"id": "3fcd2150-4e1e-4bda-b350-2f599eb3ee6a", "name": "Kling"}]),
    ],
    ids=["server-error", "not-found", "network-error", "not-json", "object", "names", "uuids"],
)
def test_an_unusable_models_response_never_fails_the_sync(higgs, serve, api_key, respx_mock, models_response):
    serve()
    route = respx_mock.get(MODELS_URL)
    if isinstance(models_response, Exception):
        route.mock(side_effect=models_response)
    else:
        route.mock(return_value=models_response)

    synced = sync(higgs)

    assert route.calls.last.request.headers["Authorization"] == api_key
    assert (synced["added"], synced["removed"], synced["from_api"]) == ([], [], [])
    assert any("GET /models" in note for note in synced["notes"])


def test_without_a_key_the_sync_does_not_call_the_api(higgs, serve, respx_mock):
    serve()

    synced = sync(higgs)

    # Every request is mocked, so a call to GET /models would have failed the test.
    assert synced["from_api"] == []
    assert synced["notes"] == []


# A guess at what GET /models might return: Endpoint objects, one the docs already have,
# and a bare path.
API_MODELS = [
    {"id": "kling-video/v4.0/text-to-video", "name": "Kling 4.0", "output_type": "video"},
    {"id": "kling-video/v3.0/pro/image-to-video", "name": "Kling 3.0"},
    "bytedance/seedream/v5/text-to-image",
]


@pytest.mark.parametrize("body", [API_MODELS, {"data": API_MODELS}], ids=["list", "wrapped-list"])
def test_endpoints_from_get_models_missing_in_the_docs_are_merged(higgs, serve, api_key, respx_mock, body):
    serve()
    respx_mock.get(MODELS_URL).respond(200, json=body)

    synced = sync(higgs)

    merged = ["bytedance/seedream/v5/text-to-image", "kling-video/v4.0/text-to-video"]
    assert synced["from_api"] == merged
    assert synced["added"] == merged
    assert "kling-4.0" in listed(higgs)
    shown = json.loads(higgs("models", "show", "kling-4.0", "--json").stdout)
    assert shown["model"]["family"] == "kling"
    [endpoint] = shown["endpoints"]
    assert endpoint["id"] == "kling-video/v4.0/text-to-video"
    assert (endpoint["kind"], endpoint["mode"], endpoint["source"]) == ("video", "t2v", "api")
    assert endpoint["parameters"] == []
    # The documented Endpoint keeps its full schema.
    kling_3 = json.loads(higgs("models", "show", "kling-3.0", "--json").stdout)["endpoints"]
    assert all(e["source"] == "docs" and e["parameters"] for e in kling_3)


def test_an_endpoint_from_get_models_takes_any_parameters_without_no_validate(
    higgs, serve, project, api_key, respx_mock
):
    serve()
    respx_mock.get(MODELS_URL).respond(200, json=API_MODELS)
    sync(higgs)
    submit = respx_mock.post("https://api.higgsfield.ai/kling-video/v4.0/text-to-video").respond(
        200, json={"status": "queued", "request_id": "d7e6c0f3-6699-4f6c-bb45-2ad7fd9158ff"}
    )

    result = higgs(
        "gen", "kling-video/v4.0/text-to-video", "-p", "prompt=Waves at dusk", "-p", "duration=7", "--no-wait", "--json"
    )

    assert result.exit_code == 0, result.output
    assert json.loads(submit.calls.last.request.content) == {"prompt": "Waves at dusk", "duration": 7}


def test_endpoints_from_get_models_that_cannot_be_placed_are_skipped_with_a_note(
    higgs, serve, api_key, respx_mock
):
    serve()
    respx_mock.get(MODELS_URL).respond(
        200,
        json=[
            # Kling 3.0 already has t2v Endpoints, one per quality tier.
            {"id": "kling-video/v3.0/text-to-video", "name": "Kling 3.0"},
            # A video Endpoint whose path names no Mode.
            {"id": "acme/v1", "type": "video"},
            # Neither the item nor the path says video or image.
            {"id": "acme/v2"},
            "kling-video/v4.0/text-to-video",
        ],
    )

    synced = sync(higgs)

    assert synced["from_api"] == ["kling-video/v4.0/text-to-video"]
    skipped = [note for note in synced["notes"] if note.startswith("Skipped")]
    assert [note.split()[1] for note in skipped] == ["kling-video/v3.0/text-to-video", "acme/v1", "acme/v2"]


@pytest.mark.parametrize(
    "content",
    ["{not json", '{"families": [], "models": [], "endpoints": [{"id": "x"}]}'],
    ids=["not-json", "not-a-catalog"],
)
def test_a_corrupt_user_catalog_falls_back_to_the_baked_in_one_with_a_warning(higgs, config_dir, content):
    (config_dir / "catalog.json").write_text(content)

    result = higgs("models", "list", "--json")

    assert result.exit_code == 0
    slugs = [model["slug"] for model in json.loads(result.stdout)["models"]]
    assert "z-image-turbo" in slugs
    assert "Warning" in result.stderr
    assert str(config_dir / "catalog.json") in result.stderr
    assert "higgs models sync" in result.stderr


def test_sync_replaces_a_corrupt_user_catalog(higgs, serve, config_dir):
    (config_dir / "catalog.json").write_text("{not json")
    serve(docs_without("z-image/turbo"))

    synced = sync(higgs)

    # Compared with the baked-in catalog, which was in effect.
    assert synced["removed"] == ["z-image/turbo"]
    result = higgs("models", "list", "--json")
    assert "Warning" not in result.stderr
    assert "z-image-turbo" not in [model["slug"] for model in json.loads(result.stdout)["models"]]
