"""Presets: `higgs presets list <catalog>` and `--preset <name>` on the convenience commands.

The API lists each Preset catalog at an authenticated endpoint (DESIGN.md §7, ADR 0004);
the response shapes and example items here are the docs' own examples.
"""

import json
from typing import Any

import httpx
import respx

API = "https://api.higgsfield.ai"

# GET /models/higgsfield/genjutsu/restyle/v1.0/presets, the docs' example ("Available styles").
RESTYLE_PRESETS = {
    "model": "higgsfield/genjutsu/restyle/v1.0",
    "items": [
        {
            "id": "c2143317-f28d-4c3c-a0b8-39bd547e08a7",
            "name": "Cel-Shaded CG Anime",
            "preview_url": "https://cdn.higgsfield.ai/restyle_presets/259d7ddb-4201-49f7-8a91-cee5bce28715.webp",
        }
    ],
}


def presets(higgs, *args: str) -> Any:
    result = higgs("presets", "list", *args, "--json")
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def error_of(result) -> dict[str, Any]:
    assert result.exit_code == 1, result.output
    error: dict[str, Any] = json.loads(result.output)["error"]
    return error


def test_an_unknown_preset_catalog_fails_with_the_catalogs_before_any_api_call(higgs, respx_mock):
    # No key is configured: the catalog name is checked first.
    error = error_of(higgs("presets", "list", "styles", "--json"))

    assert error["code"] == "unknown_preset_catalog"
    assert "soul-styles, restyle, marketing" in error["hint"]


def test_presets_list_restyle_shows_the_genjutsu_restyle_presets(higgs, api_key, respx_mock):
    route = respx_mock.get(f"{API}/models/higgsfield/genjutsu/restyle/v1.0/presets").mock(
        return_value=httpx.Response(200, json=RESTYLE_PRESETS)
    )

    output = presets(higgs, "restyle")

    assert route.calls.last.request.headers["Authorization"] == api_key
    assert output == {
        "catalog": "restyle",
        "presets": [
            {
                "id": "c2143317-f28d-4c3c-a0b8-39bd547e08a7",
                "name": "Cel-Shaded CG Anime",
                "description": None,
                "group": None,
                "aspect_ratio": None,
                "preview_url": "https://cdn.higgsfield.ai/restyle_presets/259d7ddb-4201-49f7-8a91-cee5bce28715.webp",
                "models": ["genjutsu"],
            }
        ],
    }


# GET /v1/text2image/soul-styles and /v1/text2image/soul-styles/v2: the docs' example item ("List SOUL styles"),
# with an id in place of the docs' `<style-uuid>` placeholder.
SOUL_STYLE = {
    "id": "5b2e7c0a-0000-4000-8000-000000000101",
    "name": "Editorial",
    "description": "A polished editorial image style.",
    "preview_url": "https://cdn.example.com/soul-style-preview.jpg",
}
SOUL_V2_ONLY = {"id": "5b2e7c0a-0000-4000-8000-000000000102", "name": "Film Noir", "description": "", "preview_url": None}


def test_presets_list_soul_styles_combines_the_soul_and_soul_v2_styles(higgs, api_key, respx_mock):
    respx_mock.get(f"{API}/v1/text2image/soul-styles").mock(return_value=httpx.Response(200, json=[SOUL_STYLE]))
    respx_mock.get(f"{API}/v1/text2image/soul-styles/v2").mock(
        return_value=httpx.Response(200, json=[SOUL_STYLE, SOUL_V2_ONLY])
    )

    output = presets(higgs, "soul-styles")

    assert output["catalog"] == "soul-styles"
    assert output["presets"] == [
        {
            "id": "5b2e7c0a-0000-4000-8000-000000000101",
            "name": "Editorial",
            "description": "A polished editorial image style.",
            "group": None,
            "aspect_ratio": None,
            "preview_url": "https://cdn.example.com/soul-style-preview.jpg",
            "models": ["soul", "soul-v2"],
        },
        {
            "id": "5b2e7c0a-0000-4000-8000-000000000102",
            "name": "Film Noir",
            "description": None,
            "group": None,
            "aspect_ratio": None,
            "preview_url": None,
            "models": ["soul-v2"],
        },
    ]


# GET /marketing-studio/image/presets: the docs' example item ("List Marketing Studio presets"),
# with an id in place of the docs' `<preset-uuid>` placeholder.
MARKETING_PRESET: dict[str, Any] = {
    "id": "6f1d2c3b-0000-4000-8000-000000000001",
    "type": "ads",
    "name": "Editorial product portrait",
    "cover_image": None,
    "metadata": {"aspect_ratio": "3:4", "group_name": "Editorial"},
    "format_slugs": [],
}
MARKETING_PRESET_2: dict[str, Any] = {
    "id": "6f1d2c3b-0000-4000-8000-000000000002",
    "type": "ads",
    "name": "Unboxing flat lay",
    "cover_image": "https://cdn.example.com/unboxing.webp",
    "metadata": {"aspect_ratio": "1:1", "group_name": "Product"},
    "format_slugs": [],
}
MARKETING_MODELS = [
    "marketing-studio-image-2.0-alpha",
    "marketing-studio-image-2.5-flare",
    "marketing-studio-image-2.5-sunburst",
]


def mock_marketing_presets(respx_mock, *pages: list[dict[str, Any]]):
    """The Marketing Studio presets in pages: the `cursor` query is the zero-based offset of a page."""
    offsets = [sum(len(page) for page in pages[:index]) for index in range(len(pages))]
    total = sum(len(page) for page in pages)

    def page(request: httpx.Request) -> httpx.Response:
        index = offsets.index(int(request.url.params.get("cursor", "0")))
        following = offsets[index + 1] if index + 1 < len(pages) else None
        return httpx.Response(200, json={"total": total, "cursor": following, "items": pages[index]})

    return respx_mock.get(f"{API}/marketing-studio/image/presets").mock(side_effect=page)


def test_presets_list_marketing_follows_the_pages_and_shows_group_and_aspect(higgs, api_key, respx_mock):
    route = mock_marketing_presets(respx_mock, [MARKETING_PRESET], [MARKETING_PRESET_2])

    output = presets(higgs, "marketing")

    assert [call.request.url.params.get("cursor") for call in route.calls] == ["0", "1"]
    assert output["catalog"] == "marketing"
    assert output["presets"] == [
        {
            "id": "6f1d2c3b-0000-4000-8000-000000000001",
            "name": "Editorial product portrait",
            "description": None,
            "group": "Editorial",
            "aspect_ratio": "3:4",
            "preview_url": None,
            "models": MARKETING_MODELS,
        },
        {
            "id": "6f1d2c3b-0000-4000-8000-000000000002",
            "name": "Unboxing flat lay",
            "description": None,
            "group": "Product",
            "aspect_ratio": "1:1",
            "preview_url": "https://cdn.example.com/unboxing.webp",
            "models": MARKETING_MODELS,
        },
    ]


def test_presets_list_without_json_shows_a_table_of_names_and_ids(higgs, api_key, respx_mock):
    respx_mock.get(f"{API}/models/higgsfield/genjutsu/restyle/v1.0/presets").mock(
        return_value=httpx.Response(200, json=RESTYLE_PRESETS)
    )

    result = higgs("presets", "list", "restyle")

    assert result.exit_code == 0, result.output
    assert "Cel-Shaded CG Anime" in result.output
    assert "c2143317-f28d-4c3c-a0b8-39bd547e08a7" in result.output


def submitted(request_id: str) -> httpx.Response:
    # The submit response from the docs ("Requests and lifecycle" → Initial response).
    return httpx.Response(
        200,
        json={
            "status": "queued",
            "request_id": request_id,
            "status_url": f"{API}/requests/{request_id}/status",
            "cancel_url": f"{API}/requests/{request_id}/cancel",
            "expires_after": 604800,
        },
    )


def mock_submit(respx_mock, endpoint: str):
    return respx_mock.post(f"{API}/{endpoint}").mock(return_value=submitted("req-1"))


def body(route) -> dict[str, Any]:
    sent: dict[str, Any] = json.loads(route.calls.last.request.content)
    return sent


def mock_soul_styles(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/v1/text2image/soul-styles").mock(return_value=httpx.Response(200, json=[SOUL_STYLE]))
    respx_mock.get(f"{API}/v1/text2image/soul-styles/v2").mock(
        return_value=httpx.Response(200, json=[SOUL_STYLE, SOUL_V2_ONLY])
    )


def t2i(higgs, *args: str):
    return higgs("image", "t2i", *args, "--prompt", "Editorial portrait", "--no-wait", "--json", "--yes")


def test_preset_on_soul_sends_the_style_id_of_the_named_soul_style(higgs, project, api_key, respx_mock, estimate):
    mock_soul_styles(respx_mock)
    route = mock_submit(respx_mock, "higgsfield-ai/soul/v2/standard")

    # Names match case-insensitively.
    result = t2i(higgs, "--model", "soul-v2", "--preset", "film noir")

    assert result.exit_code == 0, result.output
    assert body(route) == {"prompt": "Editorial portrait", "style_id": "5b2e7c0a-0000-4000-8000-000000000102"}


def test_preset_takes_the_raw_id_too(higgs, project, api_key, respx_mock, estimate):
    mock_soul_styles(respx_mock)
    route = mock_submit(respx_mock, "higgsfield-ai/soul/standard")

    result = t2i(higgs, "--model", "soul", "--preset", "5B2E7C0A-0000-4000-8000-000000000101")

    assert result.exit_code == 0, result.output
    assert body(route)["style_id"] == "5b2e7c0a-0000-4000-8000-000000000101"


def test_an_unknown_preset_fails_with_close_matches_and_submits_nothing(higgs, project, api_key, respx_mock, estimate):
    mock_soul_styles(respx_mock)
    route = mock_submit(respx_mock, "higgsfield-ai/soul/v2/standard")

    error = error_of(t2i(higgs, "--model", "soul-v2", "--preset", "editoral"))

    assert error["code"] == "unknown_preset"
    assert "Did you mean: Editorial?" in error["hint"]
    assert "higgs presets list soul-styles" in error["hint"]
    assert not route.called
    assert not estimate.called


def test_preset_on_a_model_without_presets_fails_before_any_api_call(higgs, project, respx_mock):
    # No key and no HTTP mocks: nothing is fetched or submitted.
    error = error_of(t2i(higgs, "--model", "grok-image-2.0", "--preset", "Editorial"))

    assert error["code"] == "unsupported_flag"
    assert error["message"] == "--preset is not supported by grok-image-2.0 t2i"
    assert "soul, soul-v2" in error["hint"]
    assert "marketing-studio-image-2.5-flare" in error["hint"]


PRODUCT = "https://cdn.example.com/product.png"


def test_preset_on_marketing_studio_sends_the_preset_id_with_prompt_enhancement(
    higgs, project, api_key, respx_mock, estimate
):
    # The docs: a preset_id takes effect through enhance_prompt=true, which needs 1–2 image URLs.
    mock_marketing_presets(respx_mock, [MARKETING_PRESET, MARKETING_PRESET_2])
    route = mock_submit(respx_mock, "marketing-studio/image/flare")

    result = t2i(
        higgs, "--model", "marketing-studio-image-2.5-flare", "--image", PRODUCT, "--preset", "Unboxing Flat Lay"
    )

    assert result.exit_code == 0, result.output
    assert body(route) == {
        "prompt": "Editorial portrait",
        "image_urls": [PRODUCT],
        "preset_id": "6f1d2c3b-0000-4000-8000-000000000002",
        "enhance_prompt": True,
    }


def test_p_overrides_the_prompt_enhancement_a_marketing_preset_turns_on(higgs, project, api_key, respx_mock, estimate):
    mock_marketing_presets(respx_mock, [MARKETING_PRESET])
    route = mock_submit(respx_mock, "marketing-studio/image")

    result = t2i(
        higgs, "--model", "marketing-studio-image-2.0-alpha", "--preset", "editorial product portrait",
        "-p", "enhance_prompt=false",
    )

    assert result.exit_code == 0, result.output
    assert body(route) == {
        "prompt": "Editorial portrait",
        "preset_id": "6f1d2c3b-0000-4000-8000-000000000001",
        "enhance_prompt": False,
    }


def test_a_preset_name_shared_by_several_presets_fails_listing_their_ids(higgs, project, api_key, respx_mock, estimate):
    # Marketing Studio presets come in groups; two groups may have a preset of the same name.
    same_name = {**MARKETING_PRESET_2, "name": "Editorial product portrait", "metadata": {"group_name": "Product"}}
    mock_marketing_presets(respx_mock, [MARKETING_PRESET, same_name])
    route = mock_submit(respx_mock, "marketing-studio/image/flare")

    error = error_of(
        t2i(higgs, "--model", "marketing-studio-image-2.5-flare", "--image", PRODUCT, "--preset", "Editorial product portrait")
    )

    assert error["code"] == "ambiguous_preset"
    assert "6f1d2c3b-0000-4000-8000-000000000001 (Editorial)" in error["hint"]
    assert "6f1d2c3b-0000-4000-8000-000000000002 (Product)" in error["hint"]
    assert not route.called
