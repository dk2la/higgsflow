"""`higgs models ...`: browse the model catalog."""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from hfcore.catalog import Endpoint, ModelSummary, Parameter, Price, PromptConstraints, load_catalog
from hfcli.output import JsonOpt, console, print_json

app = typer.Typer(name="models", help="Browse the model catalog.", no_args_is_help=True)


def format_price(price: Price | None) -> str:
    if price is None:
        return "—"
    unit = "/s" if price.unit == "second" else f"/{price.unit}"
    prefix = "from " if price.qualifier == "from" else ""
    return f"{prefix}${price.usd}{unit}"


def _modes_cell(summary: ModelSummary) -> str:
    return ", ".join(
        f"{mode} ({'/'.join(summary.tiers[mode])})" if mode in summary.tiers else mode for mode in summary.modes
    )


@app.command("list")
def list_models(
    mode: Annotated[str | None, typer.Option("--mode", help="Only Models with this Mode, e.g. i2v.")] = None,
    family: Annotated[str | None, typer.Option("--family", help="Only Models of this Family, e.g. kling.")] = None,
    as_json: JsonOpt = False,
) -> None:
    """List Models with their Family, Modes and approximate "from" price."""
    summaries = load_catalog().summaries(family=family, mode=mode)
    if as_json:
        print_json({"models": summaries})
        return
    table = Table()
    # Identifiers are typed back as `--model`, so they are never wrapped or cut.
    table.add_column("Model", no_wrap=True, min_width=max((len(s.slug) for s in summaries), default=5))
    for header in ("Name", "Family", "Modes (quality tiers)"):
        table.add_column(header)
    table.add_column("Price", no_wrap=True)
    for summary in summaries:
        table.add_row(summary.slug, summary.name, summary.family, _modes_cell(summary), format_price(summary.price))
    console.print(table)


@app.command("show")
def show_model(
    model: Annotated[str, typer.Argument(help="Model slug or name, e.g. kling-3.0.")],
    as_json: JsonOpt = False,
) -> None:
    """Show a Model's Endpoints: parameters, allowed values, defaults and prompt constraints."""
    catalog = load_catalog()
    summary = catalog.summary(model)
    endpoints = catalog.endpoints_for(summary.slug)
    if as_json:
        print_json({"model": summary, "endpoints": endpoints})
        return
    console.print(
        f"[bold]{summary.name}[/] ({summary.slug}) · family {summary.family} · {summary.kind}"
        f" · {format_price(summary.price)}",
        highlight=False,
    )
    for endpoint in endpoints:
        _print_endpoint(endpoint)


def _print_endpoint(endpoint: Endpoint) -> None:
    variant = f"{endpoint.mode} · {endpoint.tier}" if endpoint.tier else endpoint.mode
    console.print()
    console.print(f"[bold cyan]{variant}[/]  {endpoint.id}  {format_price(endpoint.price)}", highlight=False)
    if endpoint.prompt is not None:
        console.print(f"  Prompt: {_prompt_text(endpoint.prompt)}", highlight=False)
    table = Table()
    width = max((len(p.name) for p in endpoint.parameters), default=9)
    table.add_column("Parameter", no_wrap=True, min_width=width)
    for header in ("Type", "Required", "Default", "Allowed", "Description"):
        table.add_column(header)
    for parameter in endpoint.parameters:
        table.add_row(
            parameter.name,
            _type_text(parameter),
            "yes" if parameter.required else "",
            "" if parameter.default is None else escape(json.dumps(parameter.default, ensure_ascii=False)),
            _allowed_text(parameter),
            escape(parameter.description or ""),
        )
    console.print(table)
    for note in endpoint.notes:
        console.print(f"  [dim]• {escape(note)}[/]", highlight=False)


def _prompt_text(prompt: PromptConstraints) -> str:
    parts = ["required" if prompt.required else "optional"]
    if prompt.min_length is not None or prompt.max_length is not None:
        parts.append(_range(prompt.min_length, prompt.max_length, "characters"))
    if prompt.truncated_after is not None:
        parts.append(f"truncated after {prompt.truncated_after} characters")
    if prompt.negative_prompt:
        limit = f" (max {prompt.negative_max_length})" if prompt.negative_max_length else ""
        parts.append(f"negative prompt{limit}")
    if prompt.tokens:
        parts.append("tokens " + " ".join(prompt.tokens))
    return escape(", ".join(parts))


def _range(low: int | None, high: int | None, unit: str) -> str:
    if low is not None and high is not None:
        return f"{low}–{high} {unit}"
    return f"≥{low} {unit}" if low is not None else f"≤{high} {unit}"


def _type_text(parameter: Parameter) -> str:
    text = parameter.type or "any"
    if parameter.type == "array" and parameter.item_type:
        text = f"array of {parameter.item_type}"
    return f"{text} | null" if parameter.nullable else text


def _allowed_text(parameter: Parameter) -> str:
    parts = []
    if parameter.enum is not None:
        parts.append(", ".join(json.dumps(value, ensure_ascii=False) for value in parameter.enum))
    if parameter.minimum is not None or parameter.maximum is not None:
        low = "" if parameter.minimum is None else parameter.minimum
        high = "" if parameter.maximum is None else parameter.maximum
        parts.append(f"{low}..{high}")
    if parameter.multiple_of is not None:
        parts.append(f"step {parameter.multiple_of}")
    if parameter.min_length is not None or parameter.max_length is not None:
        parts.append(_range(parameter.min_length, parameter.max_length, "chars"))
    if parameter.min_items is not None or parameter.max_items is not None:
        parts.append(_range(parameter.min_items, parameter.max_items, "items"))
    if parameter.format:
        parts.append(parameter.format)
    return escape("; ".join(parts))
