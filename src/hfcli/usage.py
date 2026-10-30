"""`higgs usage`."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import typer
from rich.markup import escape
from rich.table import Table

from hfcore import projects
from hfcore import usage as core
from hfcli.output import JsonOpt, console, err_console, print_json, render_warning
from hfcli.projects import ProjectOpt, current_project


def usage(project: ProjectOpt = None, as_json: JsonOpt = False) -> None:
    """Show the Project's Spend and the account's usage summary (outside a Project: the account only)."""
    target = current_project(project) if project is not None else projects.find_project(Path.cwd())
    report = core.usage_report(target)
    if as_json:
        print_json(report)
        return
    for warning in report.warnings:
        render_warning(warning.message)
        if hint := warning.error.get("hint"):
            err_console.print(f"[dim]Hint:[/] {escape(str(hint))}", highlight=False, soft_wrap=True)
    if report.project is not None:
        _print_project(report.project)
        typer.echo()
    if report.account is not None:
        _print_account(report.account)
    typer.echo(f"The API has no balance endpoint; see the balance at {report.balance_url}")


def _print_project(local: core.ProjectUsage) -> None:
    typer.echo(f"Project {local.name}")
    typer.echo(f"Spend: ${local.spend_usd} ({local.requests} completed Request{'' if local.requests == 1 else 's'})")
    if local.budget_usd is None or local.remaining_usd is None:
        typer.echo("No Budget set (add `budget = <USD>` to project.toml)")
    elif local.remaining_usd < 0:
        typer.echo(f"Budget: ${local.budget_usd}, ${-local.remaining_usd} over")
    else:
        typer.echo(f"Budget: ${local.budget_usd}, ${local.remaining_usd} left")
    if not local.by_model:
        return
    table = Table()
    for column in ("Model", "Mode", "Requests", "Spend"):
        table.add_column(column, justify="right" if column in ("Requests", "Spend") else "left")
    for group in local.by_model:
        table.add_row(escape(group.model), escape(group.mode), str(group.requests), f"${group.spend_usd}")
    console.print(table)


def _print_account(account: core.AccountUsage) -> None:
    typer.echo(
        f"Account usage from {account.start:%Y-%m-%d} to {account.end:%Y-%m-%d} (UTC;"
        " analytics from GET /models/usage, not a billing record)"
    )
    if account.raw is not None:
        typer.echo(json.dumps(account.raw, indent=2, ensure_ascii=False))
        return
    if not account.by_model:
        typer.echo("No completed generations in this period")
        return
    table = Table()
    for column in ("Model", "Unit", "Quantity", "Cost"):
        table.add_column(column, justify="right" if column in ("Quantity", "Cost") else "left")
    for row in account.by_model:
        table.add_row(escape(row.model), escape(row.unit or ""), str(row.quantity), _money(row.cost, row.currency))
    console.print(table)
    if account.cost_usd is not None:
        typer.echo(f"Total: ${account.cost_usd}")


def _money(amount: Decimal, currency: str | None) -> str:
    return f"${amount}" if currency in (None, "USD") else f"{amount} {escape(currency)}"
