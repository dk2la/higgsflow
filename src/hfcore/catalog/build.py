"""Build `catalog.json` from the documentation snapshot.

Inputs (committed under `catalog/` in the repo):
- `llms-full.txt`: https://docs.higgsfield.ai/docs/llms-full.txt, every docs page in one file;
  each generation Endpoint has a page with `**Endpoint ID:**`, usage notes, a rendered input
  schema and the complete JSON schema.
- `pricing.json`: the pricing page's data (https://open.higgsfield.ai/pricing), fetched from
  https://dash.higgsfield.ai/api/v2/pricing/models/?page=1&page_size=50; each Endpoint's
  "from" price is its `pricing.primary` entry, keyed by the Endpoint id (`slug`).

Run inside the dev container: `python -m hfcore.catalog.build` (or `scripts/dev catalog`).

The docs do not name Modes, so the builder derives Model, Mode and tier from the docs'
model groups and the Endpoint path (rules below, with a few explicit overrides); the
resulting Catalog validates that every Model + Mode (+ tier) is exactly one Endpoint.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from hfcore.catalog.loader import BAKED_IN
from hfcore.catalog.model import (
    MODES,
    TIERS,
    Catalog,
    Endpoint,
    Family,
    Model,
    Parameter,
    Price,
    PromptConstraints,
    model_slug,
)
from hfcore.errors import HiggsError

# Endpoint path prefix → Model name, where one docs model group holds several Models.
# The first matching prefix wins, so longer prefixes come first.
MODEL_OVERRIDES = {
    "kling-video/v3.0-turbo/": "Kling 3.0 Turbo",
    "marketing-studio/image/flare": "Marketing Studio Image 2.5 Flare",
    "marketing-studio/image/sunburst": "Marketing Studio Image 2.5 Sunburst",
    "marketing-studio/image": "Marketing Studio Image 2.0 Alpha",
}
# Docs group titles spelled inconsistently in the docs.
NAME_FIXES = {"HappyHorse": "Happy Horse"}
# Brands whose name is more than one word; otherwise the Family is the first word.
MULTI_WORD_FAMILIES = ("Happy Horse", "Cinema Studio", "Marketing Studio", "Ads Studio")

# Endpoint path segment → Mode, checked in this order.
VIDEO_MODE_RULES = (
    ("motion-control", "motion"),
    ("motion-transfer", "motion"),
    ("restyle", "restyle"),
    ("object-swap", "edit"),
    ("video-extend", "extend"),
    ("video-edit", "edit"),
    ("video-reference", "vref"),
    ("reference-to-video", "ref"),
    ("image-reference", "ref"),
    ("image-to-video", "i2v"),
    ("first-last-frame", "i2v"),
    ("text-to-video", "t2v"),
)
IMAGE_MODE_RULES = (
    ("ads-studio", "ads"),
    ("edit", "i2i"),
    ("text-to-image", "t2i"),
)
# Single-workflow Endpoints whose path names no operation.
MODE_OVERRIDES = {"higgsfield/cinema-studio/4.0": "ref"}
# Image Endpoints without an operation in the path generate from text (references optional).
IMAGE_DEFAULT_MODE = "t2i"

TIER_SEGMENTS = {"fast": "fast", "std": "std", "standard": "std", "pro": "pro", "4k": "4k"}

_PAGE_SPLIT = re.compile(r"\n(?=# .+\nSource: )")
_ENDPOINT_ID = re.compile(r"^\*\*Endpoint ID:\*\* `([^`]+)`", re.M)
_BACK_LINK = re.compile(r"^\[← ([^\]]+)\]\(/docs/models/([^)]+)\)", re.M)
_SCHEMA_BLOCK = re.compile(r"> \*\*Complete JSON schema\*\*\n>\n> ```json[^\n]*\n(.*?)\n> ```", re.S)
_INPUT_SECTION = re.compile(r"^## Input schema\n(.*?)^> \*\*Complete JSON schema\*\*", re.S | re.M)
_FIELD_HEADER = re.compile(r"^\*\*([^*]+)\*\* `")
_CONSTRAINT_LINE = re.compile(
    r"^(Allowed values|Minimum|Maximum|Multiple of|Format|Each item|Minimum items|Maximum items"
    r"|Maximum characters|Minimum characters|Pattern):"
)
_OVERVIEW_LINK = re.compile(r"View API reference →\]\(/docs/models/([^)]+)\)")
_NOTES_SECTION = re.compile(r"^## Usage notes\n(.*?)(?=^## )", re.S | re.M)
# "Keep it within 2,500 characters; longer prompts are truncated." /
# "the current provider serializer uses only its first 7,000 characters."
_SOFT_LIMIT = re.compile(r"(?:within|only its first) ([\d,]+) characters")
_TOKEN = re.compile(r"<<<\w+>>>")


class CatalogBuildError(HiggsError):
    code = "catalog_build_error"


@dataclass
class _Page:
    title: str
    source: str
    body: str


def _pages(docs: str) -> list[_Page]:
    pages = []
    for chunk in _PAGE_SPLIT.split(docs):
        lines = chunk.lstrip("\n").split("\n", 2)
        if len(lines) < 2 or not lines[0].startswith("# ") or not lines[1].startswith("Source: "):
            continue
        pages.append(_Page(lines[0][2:].strip(), lines[1][8:].strip(), lines[2] if len(lines) > 2 else ""))
    return pages


def _unescape(text: str) -> str:
    """Undo the docs' Markdown escaping (`aspect\\_ratio`, `\\<\\<\\<image\\_1>>>`)."""
    return re.sub(r"\\([\\`*_{}\[\]()<>#+\-.!|])", r"\1", text)


def _kinds(pages: list[_Page]) -> dict[str, str]:
    """Docs model group slug → "video" or "image", from the two overview pages."""
    kinds: dict[str, str] = {}
    for page in pages:
        kind = {"Video Generation API": "video", "Image Generation API": "image"}.get(page.title)
        if kind:
            for group in _OVERVIEW_LINK.findall(page.body):
                kinds[group] = kind
    return kinds


def _schema(page: _Page, endpoint_id: str) -> dict[str, Any]:
    match = _SCHEMA_BLOCK.search(page.body)
    if not match:
        raise CatalogBuildError(f"No JSON schema on the docs page of {endpoint_id}.")
    lines = [line[2:] if line.startswith("> ") else line[1:] for line in match.group(1).split("\n")]
    schema: dict[str, Any] = json.loads("\n".join(lines))
    return schema


def _rendered_descriptions(page: _Page) -> dict[str, str]:
    """Field name → its prose description from the rendered "Input schema" list."""
    section = _INPUT_SECTION.search(page.body)
    if not section:
        return {}
    descriptions: dict[str, list[str]] = {}
    current: str | None = None
    for raw in section.group(1).split("\n"):
        line = raw.strip()
        header = _FIELD_HEADER.match(line)
        if header:
            name = _unescape(header.group(1))
            current = None if "[]" in name else name
            if current:
                descriptions[current] = []
        elif line.startswith("**"):
            current = None
        elif current and line and not _CONSTRAINT_LINE.match(line):
            if line != "See the complete JSON schema below.":
                descriptions[current].append(_unescape(line))
    return {name: " ".join(lines) for name, lines in descriptions.items() if lines}


def _type(prop: dict[str, Any]) -> tuple[str | None, bool]:
    """(type, nullable) of a property, folding `anyOf: [X, null]` and `[X, "null"]`."""
    declared = prop.get("type")
    if isinstance(declared, str):
        return declared, False
    if isinstance(declared, list):
        types = [t for t in declared if t != "null"]
        return (types[0] if len(types) == 1 else None), "null" in declared
    variants = prop.get("anyOf") or prop.get("oneOf") or []
    types = [v.get("type") for v in variants if v.get("type") != "null"]
    nullable = any(v.get("type") == "null" for v in variants)
    return (types[0] if len(types) == 1 and isinstance(types[0], str) else None), nullable


def _non_null_variant(prop: dict[str, Any]) -> dict[str, Any]:
    for variant in prop.get("anyOf") or prop.get("oneOf") or []:
        if variant.get("type") != "null":
            return {**variant, **{k: v for k, v in prop.items() if k not in ("anyOf", "oneOf")}}
    return prop


def _parameters(schema: dict[str, Any], descriptions: dict[str, str]) -> list[Parameter]:
    required = set(schema.get("required") or [])
    parameters = []
    for name, raw in schema.get("properties", {}).items():
        type_, nullable = _type(raw)
        prop = _non_null_variant(raw)
        items: dict[str, Any] = prop["items"] if isinstance(prop.get("items"), dict) else {}
        parameters.append(
            Parameter(
                name=name,
                type=type_,
                nullable=nullable,
                required=name in required,
                description=prop.get("description") or descriptions.get(name) or prop.get("title"),
                default=prop.get("default"),
                enum=prop.get("enum"),
                minimum=prop.get("minimum"),
                maximum=prop.get("maximum"),
                multiple_of=prop.get("multipleOf"),
                min_length=prop.get("minLength"),
                max_length=prop.get("maxLength"),
                min_items=prop.get("minItems"),
                max_items=prop.get("maxItems"),
                item_type=items.get("type") if isinstance(items.get("type"), str) else None,
                format=prop.get("format"),
            )
        )
    return parameters


def _notes(page: _Page) -> list[str]:
    section = _NOTES_SECTION.search(page.body)
    if not section:
        return []
    return [_unescape(line[2:].strip()) for line in section.group(1).split("\n") if line.startswith("* ")]


def _number(text: str) -> int:
    return int(text.replace(",", ""))


def _prompt(parameters: list[Parameter], notes: list[str]) -> PromptConstraints | None:
    by_name = {p.name: p for p in parameters}
    prompt = by_name.get("prompt")
    if prompt is None:
        return None
    negative = by_name.get("negative_prompt")
    truncated_after = None
    tokens: list[str] = []
    for note in notes:
        if "prompt" in note and (soft := _SOFT_LIMIT.search(note)):
            truncated_after = _number(soft.group(1))
        tokens += [t for t in _TOKEN.findall(note) if t not in tokens]
    return PromptConstraints(
        required=prompt.required,
        min_length=prompt.min_length,
        max_length=prompt.max_length,
        truncated_after=truncated_after,
        negative_prompt=negative is not None,
        negative_max_length=negative.max_length if negative else None,
        tokens=tokens,
    )


def _prices(pricing: dict[str, Any] | None) -> dict[str, Price]:
    """Endpoint id → its "from" price, from the pricing API response (`pricing.json`)."""
    prices: dict[str, Price] = {}
    for model in (pricing or {}).get("results", []):
        for mode in model.get("modes", []):
            primary = (mode.get("pricing") or {}).get("primary") or {}
            if primary.get("amount") is not None and primary.get("currency", "USD") == "USD":
                prices[mode["slug"]] = Price(
                    usd=Decimal(primary["amount"]),
                    unit=primary.get("unit", "second"),
                    qualifier=primary.get("qualifier", "from"),
                )
    return prices


def _model_name(endpoint_id: str, group_title: str) -> str:
    for prefix, name in MODEL_OVERRIDES.items():
        if endpoint_id.startswith(prefix):
            return name
    for wrong, right in NAME_FIXES.items():
        group_title = group_title.replace(wrong, right)
    return group_title


def _family_name(model_name: str) -> str:
    for family in MULTI_WORD_FAMILIES:
        if model_name.startswith(family):
            return family
    return re.sub(r"-\d.*$", "", model_name.split()[0])


def _mode(endpoint_id: str, kind: str) -> str:
    if endpoint_id in MODE_OVERRIDES:
        return MODE_OVERRIDES[endpoint_id]
    segments = endpoint_id.split("/")
    for segment, mode in VIDEO_MODE_RULES if kind == "video" else IMAGE_MODE_RULES:
        if segment in segments:
            return mode
    if kind == "image":
        return IMAGE_DEFAULT_MODE
    raise CatalogBuildError(f"Cannot tell the Mode of video Endpoint {endpoint_id}.")


def _tier_segment(endpoint_id: str) -> str | None:
    for segment in endpoint_id.split("/"):
        if segment in TIER_SEGMENTS:
            return TIER_SEGMENTS[segment]
    return None


def build_catalog(docs: str, pricing: dict[str, Any] | None = None) -> Catalog:
    pages = _pages(docs)
    kinds = _kinds(pages)
    prices = _prices(pricing)
    endpoints: list[Endpoint] = []
    models: dict[str, Model] = {}
    families: dict[str, Family] = {}
    for page in pages:
        id_match = _ENDPOINT_ID.search(page.body)
        if not id_match:
            continue
        endpoint_id = id_match.group(1)
        back = _BACK_LINK.search(page.body)
        if not back or back.group(2) not in kinds:
            raise CatalogBuildError(f"Cannot tell the model group of Endpoint {endpoint_id}.")
        group_title, group_slug = back.groups()
        kind = kinds[group_slug]
        model_name = _model_name(endpoint_id, group_title)
        family_name = _family_name(model_name)
        family_id, slug = model_slug(family_name), model_slug(model_name)
        family = families.setdefault(family_id, Family(id=family_id, name=family_name))
        model = models.setdefault(slug, Model(slug=slug, name=model_name, family=family.id, kind=kind))
        schema = _schema(page, endpoint_id)
        parameters = _parameters(schema, _rendered_descriptions(page))
        notes = _notes(page)
        endpoints.append(
            Endpoint(
                id=endpoint_id,
                title=re.sub(r" API$", "", page.title),
                kind=kind,
                family=family.id,
                model=model.slug,
                mode=_mode(endpoint_id, kind),
                tier=_tier_segment(endpoint_id),
                doc_url=page.source,
                parameters=parameters,
                prompt=_prompt(parameters, notes),
                price=prices.get(endpoint_id),
                notes=notes,
                input_schema=schema,
            )
        )
    if not endpoints:
        raise CatalogBuildError("The docs contain no generation Endpoints (no `**Endpoint ID:**` pages).")
    # A tier only distinguishes Endpoints when its Model has more than one.
    for slug in models:
        tiers = {e.tier for e in endpoints if e.model == slug}
        if len(tiers - {None}) < 2:
            for endpoint in endpoints:
                if endpoint.model == slug:
                    endpoint.tier = None
    endpoints.sort(key=lambda e: (e.model, MODES.index(e.mode), TIERS.index(e.tier) if e.tier else -1))
    try:
        return Catalog(
            families=sorted(families.values(), key=lambda f: f.id),
            models=sorted(models.values(), key=lambda m: m.slug),
            endpoints=endpoints,
        )
    except ValidationError as error:
        problems = "; ".join(str(e["ctx"]["error"]) if "ctx" in e else e["msg"] for e in error.errors())
        raise CatalogBuildError(f"The docs snapshot does not make a valid catalog: {problems}") from None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m hfcore.catalog.build",
        description="Build catalog.json from the documentation and pricing snapshots.",
    )
    parser.add_argument("--docs", type=Path, default=Path("catalog/llms-full.txt"))
    parser.add_argument("--pricing", type=Path, default=Path("catalog/pricing.json"))
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name(BAKED_IN))
    args = parser.parse_args(argv)
    pricing = json.loads(args.pricing.read_text(encoding="utf-8")) if args.pricing.exists() else None
    catalog = build_catalog(args.docs.read_text(encoding="utf-8"), pricing)
    args.out.write_text(catalog.model_dump_json(indent=2, exclude_defaults=True) + "\n", encoding="utf-8")
    print(f"{args.out}: {len(catalog.models)} models, {len(catalog.endpoints)} endpoints")


if __name__ == "__main__":
    main()
