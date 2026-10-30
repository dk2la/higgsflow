"""Showing and confirming the price before a submit (DESIGN.md §8 step 3).

A command that spends money takes `YesOpt` and `NoInputOpt`, estimates every Request
it is about to submit, then calls `confirm_price` once with all the prices (several
for `--count N`) before submitting any. In JSON mode it adds the returned check's
`warnings` to its output.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import typer

from hfcore import spend
from hfcore.errors import HiggsError
from hfcore.projects import Project
from hfcli.output import err_console, json_mode, no_input, render_warning


class ConfirmationRequired(HiggsError):
    """A paid action without `--yes` where nobody can be asked (`--json`, `--no-input`)."""

    code = "confirmation_required"

    def __init__(self, check: spend.PriceCheck):
        super().__init__(
            f"Submitting costs ${check.total_usd} and needs a confirmation",
            hint="Pass --yes to confirm the price; --json and --no-input never ask.",
        )
        self.check = check

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "price_usd": str(self.check.total_usd),
            "warnings": [warning.model_dump(mode="json") for warning in self.check.warnings],
        }


def confirm_price(ctx: typer.Context, project: Project, prices: Sequence[Decimal], *, yes: bool) -> spend.PriceCheck:
    """Show the total of `prices` and the Budget check, and confirm it; raise if it is not confirmed.

    `--yes` confirms without asking; going over the Budget then only warns. Without it,
    `--json` and `--no-input` fail with `confirmation_required`, and a decline with `aborted`.
    """
    check = spend.check_price(project, prices)
    if not json_mode(ctx):
        for warning in check.warnings:
            render_warning(warning.message)
    if yes:
        if not json_mode(ctx):
            err_console.print(f"Price: ${check.total_usd}", highlight=False)
        return check
    if no_input(ctx):
        raise ConfirmationRequired(check)
    requests = "1 Request" if len(prices) == 1 else f"{len(prices)} Requests"
    try:
        confirmed = typer.confirm(f"Submit {requests} for ${check.total_usd}?", default=False, err=True)
    except typer.Abort:  # end of input
        confirmed = False
    if not confirmed:
        raise HiggsError("Aborted; nothing was submitted", code="aborted")
    return check
