"""Seam 1: `higgs models list` / `higgs models show`, against the baked-in catalog.

Expected values are read off the documentation snapshot (catalog/llms-full.txt) and the
pricing snapshot (catalog/pricing.json).
"""

from __future__ import annotations

import json


def test_models_list_shows_each_model_with_family_modes_and_from_price(higgs):
    result = higgs("models", "list", "--json")

    assert result.exit_code == 0
    models = {model["slug"]: model for model in json.loads(result.output)["models"]}
    assert models["kling-3.0"] == {
        "slug": "kling-3.0",
        "name": "Kling 3.0",
        "family": "kling",
        "kind": "video",
        "modes": ["t2v", "i2v"],
        "tiers": {"t2v": ["std", "pro", "4k"], "i2v": ["std", "pro", "4k"]},
        "price": {"usd": "0.042", "unit": "second", "qualifier": "from"},
    }
    assert models["z-image-turbo"]["price"] == {"usd": "0.015", "unit": "image", "qualifier": "exact"}


def test_models_list_filters_by_mode_and_family(higgs):
    result = higgs("models", "list", "--mode", "i2v", "--family", "kling", "--json")

    assert result.exit_code == 0
    slugs = [model["slug"] for model in json.loads(result.output)["models"]]
    # Kling pages with an "Image to video" workflow, plus O3/Omni "First last frame".
    assert sorted(slugs) == ["kling-2.5-turbo", "kling-2.6", "kling-3.0", "kling-3.0-turbo", "kling-o3", "kling-omni"]


def test_models_list_filters_by_family_alone(higgs):
    result = higgs("models", "list", "--family", "seedance", "--json")

    assert [model["slug"] for model in json.loads(result.output)["models"]] == ["seedance-2.0", "seedance-2.5"]


def test_models_list_rejects_an_unknown_mode(higgs):
    result = higgs("models", "list", "--mode", "img2vid", "--json")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "unknown_mode"
    assert "i2v" in error["hint"]


def test_models_show_lists_each_endpoint_with_parameters_and_prompt_constraints(higgs):
    result = higgs("models", "show", "kling-3.0", "--json")

    assert result.exit_code == 0
    shown = json.loads(result.output)
    assert shown["model"]["slug"] == "kling-3.0"
    endpoints = {(e["mode"], e["tier"]): e for e in shown["endpoints"]}
    assert len(endpoints) == 6
    pro_i2v = endpoints[("i2v", "pro")]
    assert pro_i2v["id"] == "kling-video/v3.0/pro/image-to-video"
    parameters = {p["name"]: p for p in pro_i2v["parameters"]}
    # "**duration** `integer` (default: 5) ... Minimum: `3`. Maximum: `15`."
    assert (parameters["duration"]["default"], parameters["duration"]["minimum"]) == (5, 3)
    assert parameters["duration"]["maximum"] == 15
    # "**image_url** `string` *required*"
    assert parameters["image_url"]["required"] is True
    assert parameters["sound"]["enum"] == ["on", "off"]
    assert pro_i2v["prompt"]["truncated_after"] == 2500
    assert pro_i2v["price"] == {"usd": "0.056", "unit": "second", "qualifier": "from"}


def test_models_list_and_show_print_tables_for_humans(higgs):
    listed = higgs("models", "list", "--family", "kling")
    shown = higgs("models", "show", "z-image-turbo")

    assert listed.exit_code == shown.exit_code == 0
    assert "kling-3.0-motion-control" in listed.output
    assert "from $0.042/s" in listed.output
    assert "z-image/turbo" in shown.output
    assert "1–800 characters" in shown.output


def test_models_show_accepts_the_display_name(higgs):
    result = higgs("models", "show", "Kling 3.0", "--json")

    assert json.loads(result.output)["model"]["slug"] == "kling-3.0"


def test_models_show_suggests_close_matches_for_an_unknown_model(higgs):
    result = higgs("models", "show", "kling-3", "--json")

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "unknown_model"
    assert "kling-3.0" in error["hint"]
