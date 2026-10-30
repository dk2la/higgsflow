"""Seam 2: the catalog builder, run on the committed documentation snapshot.

Expected values are read off the documentation pages (catalog/llms-full.txt) and the
pricing snapshot (catalog/pricing.json) by hand, never recomputed the way the builder does.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from hfcore.catalog import Catalog, load_catalog
from hfcore.catalog.build import build_catalog
from hfcore.errors import HiggsError

SNAPSHOT = Path(__file__).resolve().parent.parent / "catalog"


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    docs = (SNAPSHOT / "llms-full.txt").read_text(encoding="utf-8")
    pricing = json.loads((SNAPSHOT / "pricing.json").read_text(encoding="utf-8"))
    return build_catalog(docs, pricing)


def test_kling_3_pro_image_to_video_exposes_the_sound_enum(catalog):
    endpoint = catalog.resolve("kling-3.0", "i2v", tier="pro")

    assert endpoint.id == "kling-video/v3.0/pro/image-to-video"
    sound = endpoint.parameter("sound")
    assert sound.type == "string"
    assert sound.enum == ["on", "off"]
    assert sound.default == "on"
    assert sound.description == "Enable sound for the output video."


def test_covers_every_documented_generation_endpoint(catalog):
    # The docs' model overview: "67 Video" and "17 Image" entries; one of the image
    # entries is Soul ID training (POST /v1/custom-references), which is not a generation.
    kinds = [endpoint.kind for endpoint in catalog.endpoints]

    assert kinds.count("video") == 67
    assert kinds.count("image") == 16
    assert "v1/custom-references" not in {endpoint.id for endpoint in catalog.endpoints}


def test_every_model_and_mode_resolves_to_exactly_one_endpoint(catalog):
    for model in catalog.models:
        for mode in catalog.modes(model.slug):
            tiers = catalog.tiers(model.slug, mode)
            endpoints = [catalog.resolve(model.slug, mode, tier=t) for t in tiers or [None]]

            assert len({e.id for e in endpoints}) == len(endpoints)
            assert all(e.model == model.slug and e.mode == mode for e in endpoints)
    resolved = {
        catalog.resolve(m.slug, mode, tier=t).id
        for m in catalog.models
        for mode in catalog.modes(m.slug)
        for t in catalog.tiers(m.slug, mode) or [None]
    }
    assert resolved == {endpoint.id for endpoint in catalog.endpoints}


def test_prompt_constraints_come_from_the_schema_and_the_usage_notes(catalog):
    # Z-Image Turbo: schema `"maxLength": 800, "minLength": 1`, prompt required.
    z_image = catalog.resolve("z-image-turbo", "t2i").prompt
    assert (z_image.required, z_image.min_length, z_image.max_length) == (True, 1, 800)
    assert z_image.truncated_after is None
    assert z_image.negative_prompt is False

    # Kling 3.0 Pro i2v notes: "Keep it within 2,500 characters; longer prompts are truncated."
    kling = catalog.resolve("kling-3.0", "i2v", tier="pro").prompt
    assert (kling.max_length, kling.truncated_after) == (None, 2500)

    # MiniMax H3 notes: "the current provider serializer uses only its first 7,000 characters".
    assert catalog.resolve("minimax-h3", "t2v").prompt.truncated_after == 7000

    # PixVerse V6: `negative_prompt` with `"maxLength": 2048`.
    pixverse = catalog.resolve("pixverse-v6", "t2v").prompt
    assert (pixverse.negative_prompt, pixverse.negative_max_length) == (True, 2048)

    # Cinema Studio 4.0 notes: tokens such as <<<image_1>>>, <<<video_1>>> and <<<audio_1>>>.
    cinema = catalog.resolve("cinema-studio-4.0", "ref").prompt
    assert cinema.tokens == ["<<<image_1>>>", "<<<video_1>>>", "<<<audio_1>>>"]


def test_from_prices_come_from_the_pricing_snapshot(catalog):
    # pricing.json, kling-video/v3.0/pro/image-to-video: primary amount "0.056" per second, "from".
    price = catalog.resolve("kling-3.0", "i2v", tier="pro").price
    assert (price.usd, price.unit, price.qualifier) == (Decimal("0.056"), "second", "from")

    # SOUL V2: "0.0032" per image.
    assert catalog.resolve("soul-v2", "t2i").price.usd == Decimal("0.0032")

    # Kling 3.0's cheapest Endpoints are the Standard ones at "0.042" per second.
    model_price = catalog.price_from("kling-3.0")
    assert (model_price.usd, model_price.unit, model_price.qualifier) == (Decimal("0.042"), "second", "from")

    # Genjutsu Restyle is missing from the pricing page.
    assert catalog.resolve("genjutsu", "restyle").price is None


def test_the_committed_catalog_is_the_build_of_the_committed_snapshot(catalog, tmp_path):
    # If this fails, rebuild it: `scripts/dev catalog`.
    assert load_catalog(tmp_path) == catalog


def _endpoint_page(endpoint_id: str) -> str:
    return f"""# Acme 1.0 — {endpoint_id} API
Source: https://docs.higgsfield.ai/docs/models/acme-1/x

[← Acme 1.0](/docs/models/acme-1)

**Endpoint ID:** `{endpoint_id}`

> **Complete JSON schema**
>
> ```json
> {{"type": "object", "properties": {{"prompt": {{"type": "string"}}}}}}
> ```
"""


def test_a_snapshot_where_two_endpoints_share_a_model_and_mode_is_rejected():
    docs = "\n".join(
        [
            "# Video Generation API\nSource: https://docs.higgsfield.ai/docs/models/video-generation\n\n"
            "[Acme 1.0\nView API reference →](/docs/models/acme-1)\n",
            _endpoint_page("acme/v1.0/a/text-to-video"),
            _endpoint_page("acme/v1.0/b/text-to-video"),
        ]
    )

    with pytest.raises(HiggsError) as raised:
        build_catalog(docs)

    assert "acme/v1.0/a/text-to-video" in raised.value.message
    assert "acme/v1.0/b/text-to-video" in raised.value.message


@pytest.mark.parametrize(
    ("model", "mode", "tier", "endpoint_id"),
    [
        # Workflow tables of the docs model pages, with the Mode each workflow maps to.
        ("kling-o3", "i2v", None, "kling-video/o3/first-last-frame"),
        ("kling-o3", "ref", None, "kling-video/o3/image-reference"),
        ("kling-o3", "vref", None, "kling-video/o3/video-reference"),
        ("kling-o3", "edit", None, "kling-video/o3/video-edit"),
        ("kling-3.0-turbo", "t2v", None, "kling-video/v3.0-turbo/text-to-video"),
        ("kling-3.0", "t2v", "4k", "kling-video/v3.0/4k/text-to-video"),
        ("kling-3.0-motion-control", "motion", "std", "kling-video/v3/motion-control/std"),
        ("ltx-2.5", "i2v", "fast", "lightricks/ltx-2.5/image-to-video/fast"),
        ("genjutsu", "motion", None, "higgsfield/genjutsu/motion-transfer/v1.0"),
        ("genjutsu", "edit", None, "higgsfield/genjutsu/object-swap/v1.0"),
        ("genjutsu", "restyle", None, "higgsfield/genjutsu/restyle/v1.0"),
        ("seedance-2.5", "extend", None, "bytedance/seedance-2.5/video-extend"),
        ("cinema-studio-4.0", "ref", None, "higgsfield/cinema-studio/4.0"),
        ("qwen-image-3", "i2i", None, "alibaba/qwen-image-3/edit"),
        ("marketing-studio-image-2.5-flare", "t2i", None, "marketing-studio/image/flare"),
        ("ads-studio", "ads", None, "higgsfield/ads-studio/v1.0"),
    ],
)
def test_documented_workflows_map_to_models_and_modes(catalog, model, mode, tier, endpoint_id):
    assert catalog.resolve(model, mode, tier=tier).id == endpoint_id


def test_kling_3_offers_three_quality_tiers_per_mode(catalog):
    # Kling 3.0 page: "Standard, Pro, 4K and Turbo workflow variants"; Turbo is its own Model.
    assert catalog.tiers("kling-3.0", "i2v") == ["std", "pro", "4k"]
    assert catalog.model("kling-3.0").family == catalog.model("kling-3.0-turbo").family == "kling"
