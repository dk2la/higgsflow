"""Preset catalogs: Soul styles, Genjutsu Restyle presets, Marketing Studio presets (DESIGN.md §7, ADR 0004).

The docs list no Presets, only the authenticated API endpoints that do, so a catalog is
always fetched live with the key; nothing is baked in or cached. `SOURCES` is the table:
per list endpoint, the catalog it belongs to and the Endpoints whose Requests take its
ids. A catalog may have several sources (SOUL and SOUL V2 list their styles separately).
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from hfcore.api import ApiClient
from hfcore.catalog import Catalog, Endpoint
from hfcore.errors import HiggsError


@dataclass(frozen=True)
class PresetSource:
    """One API list of Presets: its catalog, its `GET` path, and the Endpoint ids that take its ids.

    A `paginated` list is read page by page (`size`, `cursor` = offset, until the response's
    `cursor` is null); the others return everything at once. `params` go into the body of a
    Request that uses one of its Presets, below `-p`.
    """

    catalog: str
    path: str
    endpoints: tuple[str, ...]
    paginated: bool = False
    params: dict[str, Any] = field(default_factory=dict)


SOURCES: tuple[PresetSource, ...] = (
    PresetSource("soul-styles", "/v1/text2image/soul-styles", ("higgsfield-ai/soul/standard",)),
    PresetSource("soul-styles", "/v1/text2image/soul-styles/v2", ("higgsfield-ai/soul/v2/standard",)),
    PresetSource("restyle", "/models/higgsfield/genjutsu/restyle/v1.0/presets", ("higgsfield/genjutsu/restyle/v1.0",)),
    PresetSource(
        "marketing",
        "/marketing-studio/image/presets",
        ("marketing-studio/image", "marketing-studio/image/flare", "marketing-studio/image/sunburst"),
        paginated=True,
        # The docs: a Marketing Studio preset_id is used by prompt enhancement, which needs 1–2 images.
        params={"enhance_prompt": True},
    ),
)

# The largest page the Marketing Studio list allows.
PAGE_SIZE = 100

CATALOGS: tuple[str, ...] = tuple(dict.fromkeys(source.catalog for source in SOURCES))


class Preset(BaseModel):
    """One Preset as the API lists it. `models` are the catalog Model slugs (`--model`) that take it."""

    id: str
    name: str
    description: str | None = None
    group: str | None = None
    aspect_ratio: str | None = None
    preview_url: str | None = None
    models: list[str] = []


def catalog_sources(catalog_name: str) -> list[PresetSource]:
    """The sources of a Preset catalog; an unknown catalog name is an `unknown_preset_catalog` error."""
    sources = [source for source in SOURCES if source.catalog == catalog_name]
    if not sources:
        raise HiggsError(
            f"Unknown Preset catalog {catalog_name!r}",
            hint=f"Preset catalogs: {', '.join(CATALOGS)}.",
            code="unknown_preset_catalog",
        )
    return sources


def list_presets(client: ApiClient, catalog_name: str, catalog: Catalog) -> list[Preset]:
    """Every Preset of a catalog (`soul-styles`, `restyle`, `marketing`), fetched live.

    A Preset in several lists of the catalog (a style of both SOUL and SOUL V2) is listed
    once, with the Models of all of them.
    """
    return _merge(client, catalog_sources(catalog_name), catalog)


def endpoint_sources(endpoint: Endpoint, catalog: Catalog) -> list[PresetSource]:
    """The Preset lists whose ids the Endpoint takes; none is an `unsupported_flag` error for `--preset`."""
    sources = [source for source in SOURCES if endpoint.id in source.endpoints]
    if sources:
        return sources
    by_catalog = {
        name: list(dict.fromkeys(slug for source in catalog_sources(name) for slug in _models(source, catalog)))
        for name in CATALOGS
    }
    supported = "; ".join(f"{name}: {', '.join(models)}" for name, models in by_catalog.items() if models)
    raise HiggsError(
        f"--preset is not supported by {endpoint.model} {endpoint.mode}",
        hint=f"Presets apply to these Models ({supported}); see `higgs presets list <catalog>`.",
        code="unsupported_flag",
    )


def preset_params(endpoint: Endpoint) -> dict[str, Any]:
    """The parameters a Preset brings along on the Endpoint (Marketing Studio: `enhance_prompt`)."""
    params: dict[str, Any] = {}
    for source in SOURCES:
        if endpoint.id in source.endpoints:
            params.update(source.params)
    return params


def find_preset(client: ApiClient, endpoint: Endpoint, wanted: str, catalog: Catalog) -> Preset:
    """The Preset `wanted` (a name, case-insensitively, or an id) among those the Endpoint takes.

    Fetches the Endpoint's lists live, so a Preset is checked against what the API offers now.
    """
    sources = endpoint_sources(endpoint, catalog)
    found = _merge(client, sources, catalog)
    key = wanted.strip().lower()
    matches = [preset for preset in found if preset.id.lower() == key] or [
        preset for preset in found if preset.name.lower() == key
    ]
    if len(matches) > 1:
        listed = ", ".join(f"{preset.id} ({preset.group})" if preset.group else preset.id for preset in matches)
        raise HiggsError(
            f"Several Presets are named {wanted!r}",
            hint=f"Pass the id of one: {listed}.",
            code="ambiguous_preset",
        )
    if matches:
        return matches[0]
    names = list(dict.fromkeys(preset.name for preset in found))
    by_lower = {name.lower(): name for name in names}
    close = [by_lower[name] for name in difflib.get_close_matches(key, list(by_lower), n=5, cutoff=0.6)]
    suggestion = f"Did you mean: {', '.join(close)}? " if close else ""
    raise HiggsError(
        f"No Preset {wanted!r} for {endpoint.model} {endpoint.mode}",
        hint=f"{suggestion}See `higgs presets list {sources[0].catalog}` for the names and ids.",
        code="unknown_preset",
    )


def _merge(client: ApiClient, sources: list[PresetSource], catalog: Catalog) -> list[Preset]:
    found: dict[str, Preset] = {}
    for source in sources:
        for preset in _fetch(client, source, catalog):
            known = found.get(preset.id)
            if known is None:
                found[preset.id] = preset
            else:
                known.models = list(dict.fromkeys([*known.models, *preset.models]))
    return list(found.values())


def _fetch(client: ApiClient, source: PresetSource, catalog: Catalog) -> list[Preset]:
    models = _models(source, catalog)
    if not source.paginated:
        return [_preset(item, models) for item in _items(client.list_presets(source.path), source)]
    found: list[Preset] = []
    cursor = 0
    while True:
        body = client.list_presets(source.path, {"size": PAGE_SIZE, "cursor": cursor})
        items = _items(body, source)
        found += [_preset(item, models) for item in items]
        following = body.get("cursor")
        # A stuck or missing cursor ends the listing instead of looping.
        if not items or not isinstance(following, int) or following <= cursor:
            return found
        cursor = following


def _items(body: Any, source: PresetSource) -> list[dict[str, Any]]:
    items = body.get("items") if isinstance(body, dict) else body
    if not isinstance(items, list):
        raise HiggsError(
            f"Unexpected response from {source.path}: no list of Presets",
            hint="The API may have changed; please report it.",
            code="invalid_response",
        )
    return [item for item in items if isinstance(item, dict) and isinstance(item.get("id"), str)]


def _preset(item: dict[str, Any], models: list[str]) -> Preset:
    """A list item: SOUL `{id, name, description, preview_url}`, Restyle `{id, name, preview_url}`,
    Marketing Studio `{id, name, cover_image, metadata: {aspect_ratio, group_name}}`."""
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return Preset(
        id=item["id"],
        name=str(item.get("name") or item["id"]),
        description=_text(item.get("description")),
        group=_text(metadata.get("group_name")),
        aspect_ratio=_text(metadata.get("aspect_ratio")),
        preview_url=_text(item.get("preview_url")) or _text(item.get("cover_image")),
        models=models,
    )


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _models(source: PresetSource, catalog: Catalog) -> list[str]:
    """The Model slugs of the source's Endpoints that the catalog has."""
    slugs = []
    for endpoint_id in source.endpoints:
        try:
            slugs.append(catalog.endpoint(endpoint_id).model)
        except HiggsError:
            continue
    return list(dict.fromkeys(slugs))
