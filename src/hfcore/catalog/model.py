"""The catalog data model: Family → Model → (Model, Mode[, tier]) = exactly one Endpoint.

Endpoints are the source of truth; a Model's Modes, tiers and "from" price are derived
from its Endpoints, so a layer that adds Endpoints (the user catalog, `models.local.toml`)
never has to keep derived fields in sync.
"""

from __future__ import annotations

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
    """One Higgsfield API path with its own parameter schema."""

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
        try:
            return self._by_slug[name]
        except KeyError:
            raise HiggsError(f"Unknown model {name!r}.", code="unknown_model") from None

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
        return Price(usd=cheapest.usd, unit=cheapest.unit, qualifier="from")

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


def _mode_order(mode: str) -> tuple[int, str]:
    return (MODES.index(mode) if mode in MODES else len(MODES), mode)


def _tier_order(tier: str) -> tuple[int, str]:
    return (TIERS.index(tier) if tier in TIERS else len(TIERS), tier)
