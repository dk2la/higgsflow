"""`higgs models sync`: rebuild the catalog from the live docs into the user's config dir.

The sync downloads the same two sources the baked-in catalog is built from (see `build.py`),
builds a Catalog and writes it to `<config dir>/catalog.json` atomically. A docs download
or build failure fails the sync before anything is written, so the previous user catalog
(or the baked-in one) stays in effect.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from hfcore.catalog.build import CatalogBuildError, build_catalog
from hfcore.catalog.loader import load_base_catalog, user_catalog_path
from hfcore.catalog.model import Catalog
from hfcore.errors import HiggsError

DOCS_URL = "https://docs.higgsfield.ai/docs/llms-full.txt"
PRICING_URL = "https://dash.higgsfield.ai/api/v2/pricing/models/?page=1&page_size=50"
TIMEOUT = httpx.Timeout(60.0, connect=10.0)
# A guard against a `next` link that never ends; the pricing page has about 35 Models.
MAX_PRICING_PAGES = 20

_KEPT = "The catalog in use is unchanged."


class CatalogSyncError(HiggsError):
    code = "catalog_sync_failed"


class SyncResult(BaseModel):
    """What a sync wrote, and which Endpoint ids it added or removed vs the previous base catalog."""

    path: str
    models: int
    endpoints: int
    added: list[str]
    removed: list[str]
    notes: list[str]


def sync_catalog(config_dir: Path) -> SyncResult:
    previous = load_base_catalog(config_dir)
    notes: list[str] = []
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as http:
        docs = _get(http, DOCS_URL, "the docs").text
        try:
            pricing = _pricing(http)
        except (CatalogSyncError, ValueError, AttributeError) as error:
            pricing = None
            reason = error.message if isinstance(error, HiggsError) else f"unexpected response ({error})"
            notes.append(f"No pricing data ({reason}); kept the prices of the previous catalog.")
    try:
        catalog = build_catalog(docs, pricing)
    except CatalogBuildError as error:
        raise CatalogSyncError(
            f"Cannot build the catalog from the docs at {DOCS_URL}: {error.message}",
            hint=f"{_KEPT} The docs format may have changed; try again later or report it.",
        ) from None
    if pricing is None:
        _carry_over_prices(catalog, previous)
    before = {endpoint.id for endpoint in previous.endpoints}
    current = {endpoint.id for endpoint in catalog.endpoints}
    path = user_catalog_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(catalog.model_dump_json(indent=2, exclude_defaults=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return SyncResult(
        path=str(path),
        models=len(catalog.models),
        endpoints=len(catalog.endpoints),
        added=sorted(current - before),
        removed=sorted(before - current),
        notes=notes,
    )


def _carry_over_prices(catalog: Catalog, previous: Catalog) -> None:
    prices = {endpoint.id: endpoint.price for endpoint in previous.endpoints}
    for endpoint in catalog.endpoints:
        endpoint.price = prices.get(endpoint.id)


def _pricing(http: httpx.Client) -> dict[str, Any]:
    """Every pricing page merged into one response (a DRF-style list: `results`, `next`)."""
    results: list[Any] = []
    url: str | None = PRICING_URL
    for _ in range(MAX_PRICING_PAGES):
        if url is None:
            break
        page = _get(http, url, "the pricing data").json()
        results += page.get("results") or []
        url = page.get("next")
    return {"results": results}


def _get(http: httpx.Client, url: str, what: str) -> httpx.Response:
    try:
        response = http.get(url)
    except httpx.HTTPError as error:
        raise CatalogSyncError(
            f"Cannot download {what} from {url}: {error}",
            hint=f"{_KEPT} Check the network connection and try again.",
        ) from None
    if response.is_error:
        raise CatalogSyncError(
            f"Cannot download {what} from {url}: HTTP {response.status_code}",
            hint=f"{_KEPT} Try again later.",
        )
    return response
