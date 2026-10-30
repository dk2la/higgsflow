"""Make sense of the undocumented `GET /models` response, whatever its format.

Nothing about the response is known (DESIGN.md §6), so this reads it defensively: a list,
or an object holding a list under a common key, of Endpoint paths or of objects with a
path under a common key. Anything that does not look like an Endpoint path (a bare name,
a UUID, a number) is ignored. Each Endpoint the docs lack becomes a minimal Endpoint with
`source="api"`: no parameters and an empty schema, so validation lets any parameters through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, TypeGuard

from pydantic import ValidationError

from hfcore.api import BASE_URL
from hfcore.catalog.build import CatalogBuildError, family_of, mode_of
from hfcore.catalog.model import MODES, Catalog, Endpoint, Family, Model, model_slug

# Where a response may keep its list, and an item its Endpoint path, name, kind and Mode.
_LIST_KEYS = ("models", "data", "results", "items", "endpoints")
_PATH_KEYS = ("endpoint_id", "endpoint", "path", "model_id", "id", "slug", "model")
_NAME_KEYS = ("name", "title", "display_name")
_KIND_KEYS = ("kind", "type", "output_type", "category")
# Two or more path segments, like every documented Endpoint id (`z-image/turbo`).
_ENDPOINT_PATH = re.compile(r"^[a-z0-9][\w.-]*(?:/[\w.-]+)+$", re.I)

API_ENDPOINT_NOTE = (
    "Listed by GET /models but not in the docs: no parameter schema, so its parameters are"
    " not validated; pass them with -p key=value."
)


@dataclass
class ApiEndpoint:
    """What a `GET /models` item tells about one Endpoint."""

    id: str
    name: str | None
    kind: str | None
    mode: str | None


def api_endpoints(body: Any) -> list[ApiEndpoint]:
    """The Endpoints a `GET /models` response lists, in order and without duplicates."""
    items = body
    if isinstance(body, dict):
        items = next((body[key] for key in _LIST_KEYS if isinstance(body.get(key), list)), [])
    if not isinstance(items, list):
        return []
    found: dict[str, ApiEndpoint] = {}
    for item in items:
        entry = _api_endpoint(item)
        if entry is not None and entry.id not in found:
            found[entry.id] = entry
    return list(found.values())


def merge_api_endpoints(
    catalog: Catalog, entries: list[ApiEndpoint], notes: list[str]
) -> tuple[Catalog, list[str]]:
    """The catalog with the entries the docs lack added; also their ids.

    An entry that cannot be placed (unknown kind or Mode, or a Model + Mode the catalog
    already has) is skipped with a note rather than guessed.
    """
    known = {endpoint.id for endpoint in catalog.endpoints}
    added: list[str] = []
    for entry in entries:
        if entry.id in known:
            continue
        try:
            catalog = _with_endpoint(catalog, entry)
        except CatalogBuildError as error:
            notes.append(f"Skipped {entry.id} from GET /models: {error.message}")
            continue
        added.append(entry.id)
    return catalog, sorted(added)


def _api_endpoint(item: Any) -> ApiEndpoint | None:
    if isinstance(item, str):
        item = {"id": item}
    if not isinstance(item, dict):
        return None
    endpoint_id = next((path for key in _PATH_KEYS if (path := _path(item.get(key)))), None)
    if endpoint_id is None:
        return None
    name = next((value.strip() for key in _NAME_KEYS if _text(value := item.get(key))), None)
    hints = [str(item[key]).lower() for key in _KIND_KEYS if _text(item.get(key))] + [endpoint_id]
    kind = next((kind for hint in hints for kind in ("video", "image") if kind in hint), None)
    mode = item.get("mode") if item.get("mode") in MODES else None
    return ApiEndpoint(id=endpoint_id, name=name, kind=kind, mode=mode)


def _text(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())


def _path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    path = value.strip().removeprefix(BASE_URL).strip("/")
    return path if _ENDPOINT_PATH.match(path) else None


def _with_endpoint(catalog: Catalog, entry: ApiEndpoint) -> Catalog:
    if entry.kind is None:
        raise CatalogBuildError("cannot tell whether it makes video or images")
    mode = entry.mode or mode_of(entry.id, entry.kind)
    name = entry.name or entry.id
    slug = model_slug(name)
    families, models = list(catalog.families), list(catalog.models)
    model = next((model for model in models if model.slug == slug), None)
    if model is None:
        family = family_of(entry.name) if entry.name else entry.id.split("/")[0]
        family_id = model_slug(family)
        if all(known.id != family_id for known in families):
            families.append(Family(id=family_id, name=family))
        model = Model(slug=slug, name=name, family=family_id, kind=entry.kind)
        models.append(model)
    endpoint = Endpoint(
        id=entry.id,
        title=name,
        kind=entry.kind,
        family=model.family,
        model=model.slug,
        mode=mode,
        parameters=[],
        notes=[API_ENDPOINT_NOTE],
        input_schema={},
        source="api",
    )
    try:
        return Catalog(
            families=sorted(families, key=lambda f: f.id),
            models=sorted(models, key=lambda m: m.slug),
            endpoints=[*catalog.endpoints, endpoint],
        )
    except ValidationError as error:
        problems = "; ".join(str(e["ctx"]["error"]) if "ctx" in e else e["msg"] for e in error.errors())
        raise CatalogBuildError(problems) from None
