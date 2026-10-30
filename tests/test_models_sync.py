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
