"""The catalog data model: Family → Model → (Model, Mode[, tier]) = exactly one Endpoint.

Endpoints are the source of truth; a Model's Modes, tiers and "from" price are derived
from its Endpoints, so a layer that adds Endpoints (the user catalog, `models.local.toml`)
never has to keep derived fields in sync.
"""

from __future__ import annotations

import difflib
import re
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, PrivateAttr, model_validator

from hfcore.errors import HiggsError

# Mode codes in display order (see DESIGN.md §6).
VIDEO_MODES = ("t2v", "i2v", "ref", "vref", "edit", "extend", "motion", "restyle")
IMAGE_MODES = ("t2i", "i2i", "ads")
MODES = VIDEO_MODES + IMAGE_MODES

# Tiers are the quality axis some Models encode in the Endpoint path, lowest first.
TIERS = ("fast", "std", "pro", "4k")


class Parameter(BaseModel):
    """One top-level field of an Endpoint's request body."""

    name: str
    type: str | None = None
    nullable: bool = False
    required: bool = False
    description: str | None = None
    default: Any = None
    enum: list[Any] | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    multiple_of: int | float | None = None
    min_length: int | None = None
    max_length: int | None = None
    min_items: int | None = None
    max_items: int | None = None
    item_type: str | None = None
    format: str | None = None


class PromptConstraints(BaseModel):
    """What an Endpoint accepts in its `prompt` (and `negative_prompt`).

    `max_length` is a hard limit from the schema (longer prompts are rejected);
    `truncated_after` is a soft one from the usage notes (the provider silently keeps only
    that many characters). `tokens` are the placeholders the prompt may use to refer to
    attached media, such as `<<<image_1>>>`.
    """

    required: bool = False
    min_length: int | None = None
    max_length: int | None = None
    truncated_after: int | None = None
    negative_prompt: bool = False
    negative_max_length: int | None = None
    tokens: list[str] = []


class Price(BaseModel):
    """An approximate price from the pricing page, in USD per `unit` ("second" or "image").

    `qualifier` is "from" when the price depends on the configuration (resolution,
    duration, ...) and this is the lowest, "exact" when it does not. The exact price of a
    Request always comes from the estimate endpoint.
    """

    usd: Decimal
    unit: str
    qualifier: str = "from"


class Endpoint(BaseModel):
    """One Higgsfield API path with its own parameter schema.

    `source` says where the Endpoint comes from: "docs" (the documentation, full schema),
    "local" (`models.local.toml`) or "api" (listed only by the undocumented `GET /models`
    during `higgs models sync`: no parameters and an empty `input_schema`, so validation lets
    any parameters through).
    """

    id: str
    title: str
    kind: str
    family: str
    model: str
    mode: str
    tier: str | None = None
    doc_url: str | None = None
    parameters: list[Parameter]
    prompt: PromptConstraints | None = None
    price: Price | None = None
    notes: list[str] = []
    input_schema: dict[str, Any]
    source: str = "docs"

    def parameter(self, name: str) -> Parameter:
        for parameter in self.parameters:
            if parameter.name == name:
                return parameter
        raise HiggsError(
            f"Endpoint {self.id} has no parameter {name!r}.",
            hint="Parameters: " + ", ".join(p.name for p in self.parameters),
            code="unknown_parameter",
        )


class Model(BaseModel):
    """A specific version within a Family, such as Kling 3.0."""

    slug: str
    name: str
    family: str
    kind: str


class Family(BaseModel):
    """A brand of Models, such as Kling."""

    id: str
    name: str


class ModelSummary(BaseModel):
    """A Model with what its Endpoints add up to: Modes, tiers per Mode, "from" price."""

    slug: str
    name: str
    family: str
    kind: str
    modes: list[str]
    tiers: dict[str, list[str]]
    price: Price | None


class Catalog(BaseModel):
    """Every known Endpoint, grouped into Models and Families.

    Constructing one checks the invariant: a Model + Mode is exactly one Endpoint, or, for
    a Model with quality tiers in that Mode, Model + Mode + tier is.
    """

    families: list[Family]
    models: list[Model]
    endpoints: list[Endpoint]

    _by_id: dict[str, Endpoint] = PrivateAttr(default_factory=dict)
    _by_slug: dict[str, Model] = PrivateAttr(default_factory=dict)

    @model_validator(mode="after")
    def _index(self) -> Catalog:
        self._by_id = {endpoint.id: endpoint for endpoint in self.endpoints}
        self._by_slug = {model.slug: model for model in self.models}
        family_ids = {family.id for family in self.families}
        for model in self.models:
            if model.family not in family_ids:
                raise ValueError(f"model {model.slug} has an unknown family {model.family!r}")
        groups: dict[tuple[str, str], list[Endpoint]] = {}
        for endpoint in self.endpoints:
            if endpoint.model not in self._by_slug:
                raise ValueError(f"endpoint {endpoint.id} has an unknown model {endpoint.model!r}")
            groups.setdefault((endpoint.model, endpoint.mode), []).append(endpoint)
        for (model_slug, mode), group in groups.items():
            tiers = [endpoint.tier for endpoint in group]
            if len(group) > 1 and (None in tiers or len(set(tiers)) != len(tiers)):
                ids = ", ".join(endpoint.id for endpoint in group)
                raise ValueError(f"model {model_slug} mode {mode} maps to several endpoints: {ids}")
        return self

    def model(self, name: str) -> Model:
        """A Model by slug (`kling-3.0`) or display name (`Kling 3.0`), case-insensitively."""
        found = self._by_slug.get(name) or self._by_slug.get(model_slug(name))
        if found is None:
            raise HiggsError(
                f"Unknown model {name!r}.",
                hint=_suggest(model_slug(name), list(self._by_slug), "higgs models list"),
                code="unknown_model",
            )
        return found

    def endpoint(self, endpoint_id: str) -> Endpoint:
        """An Endpoint by its id, the API path (`kling-video/v3.0/pro/image-to-video`)."""
        found = self._by_id.get(endpoint_id.strip("/"))
        if found is None:
            raise HiggsError(
                f"Unknown endpoint {endpoint_id!r}.",
                hint=_suggest(endpoint_id, list(self._by_id), "higgs models show <model>"),
                code="unknown_endpoint",
            )
        return found

    def endpoints_for(self, model: str, mode: str | None = None) -> list[Endpoint]:
        slug = self.model(model).slug
        return [e for e in self.endpoints if e.model == slug and (mode is None or e.mode == mode)]

    def modes(self, model: str) -> list[str]:
        """The Model's Modes, in `MODES` order."""
        present = {e.mode for e in self.endpoints_for(model)}
        return sorted(present, key=_mode_order)

    def tiers(self, model: str, mode: str) -> list[str]:
        """Tiers the Model offers for the Mode, lowest first; empty when it has one Endpoint."""
        present = {e.tier for e in self.endpoints_for(model, mode) if e.tier is not None}
        return sorted(present, key=_tier_order)

    def price_from(self, model: str) -> Price | None:
        """The lowest price over the Model's Endpoints, or None when none is priced."""
        prices = [e.price for e in self.endpoints_for(model) if e.price is not None]
        if not prices:
            return None
        cheapest = min(prices, key=lambda price: price.usd)
        if all(price == cheapest for price in prices) and cheapest.qualifier == "exact":
            return cheapest
        return Price(usd=cheapest.usd, unit=cheapest.unit, qualifier="from")

    def summary(self, model: str) -> ModelSummary:
        found = self.model(model)
        modes = self.modes(found.slug)
        return ModelSummary(
            slug=found.slug,
            name=found.name,
            family=found.family,
            kind=found.kind,
            modes=modes,
            tiers={mode: tiers for mode in modes if (tiers := self.tiers(found.slug, mode))},
            price=self.price_from(found.slug),
        )

    def summaries(self, family: str | None = None, mode: str | None = None) -> list[ModelSummary]:
        """Models, optionally only those of a Family and/or having a Mode."""
        family_id = self.family(family).id if family is not None else None
        if mode is not None and mode not in {e.mode for e in self.endpoints}:
            raise HiggsError(
                f"Unknown mode {mode!r}.", hint="Modes: " + ", ".join(self.all_modes()), code="unknown_mode"
            )
        found = [self.summary(model.slug) for model in self.models if family_id in (None, model.family)]
        return [summary for summary in found if mode is None or mode in summary.modes]

    def all_modes(self) -> list[str]:
        return sorted({e.mode for e in self.endpoints}, key=_mode_order)

    def family(self, name: str) -> Family:
        """A Family by id or name, case-insensitively."""
        wanted = name.strip().lower()
        for family in self.families:
            if wanted in (family.id, family.name.lower()):
                return family
        raise HiggsError(
            f"Unknown family {name!r}.",
            hint="Families: " + ", ".join(family.id for family in self.families),
            code="unknown_family",
        )

    def resolve(self, model: str, mode: str, tier: str | None = None) -> Endpoint:
        """The one Endpoint for Model + Mode (+ tier, when the Model has tiers for the Mode)."""
        found = self.model(model)
        candidates = self.endpoints_for(found.slug, mode)
        if not candidates:
            raise HiggsError(
                f"Model {found.slug} has no Endpoint for mode {mode!r}.",
                hint="Its modes: " + ", ".join(self.modes(found.slug)),
                code="unknown_mode",
            )
        if tier is not None:
            candidates = [e for e in candidates if e.tier == tier]
        if len(candidates) != 1:
            tiers = ", ".join(self.tiers(found.slug, mode))
            if tier is None:
                message = f"Model {found.slug} {mode} needs a quality tier."
            else:
                message = f"Model {found.slug} {mode} has no quality tier {tier!r}."
            raise HiggsError(message, hint=f"Its tiers: {tiers}", code="unknown_tier")
        return candidates[0]


def model_slug(name: str) -> str:
    """The Model slug for a display name: `Kling 3.0 Turbo` → `kling-3.0-turbo`."""
    return re.sub(r"[^a-z0-9.]+", "-", name.lower()).strip("-")


def _suggest(wanted: str, known: list[str], fallback: str) -> str:
    close = difflib.get_close_matches(wanted, known, n=5, cutoff=0.6)
    close += [name for name in known if name.startswith(wanted) and name not in close]
    if close:
        return "Did you mean: " + ", ".join(close[:5]) + "?"
    return f"See `{fallback}`."


def _mode_order(mode: str) -> tuple[int, str]:
    return (MODES.index(mode) if mode in MODES else len(MODES), mode)


def _tier_order(tier: str) -> tuple[int, str]:
    return (TIERS.index(tier) if tier in TIERS else len(TIERS), tier)
