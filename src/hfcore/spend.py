"""Spend and Budget (GLOSSARY.md, DESIGN.md §8 step 3).

Spend is computed from the Request records (ADR 0002): the sum of the estimated prices
of the Project's `completed` Requests; `failed`, `nsfw` and `canceled` ones are not
billed, and pending ones are not counted until they complete. A Budget only warns.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel

from hfcore.projects import Project, load_config
from hfcore.requests import RequestRecord, list_requests


class BudgetWarning(BaseModel):
    """Submitting would take the Project's Spend over its Budget."""

    code: Literal["over_budget"] = "over_budget"
    message: str
    spend_usd: Decimal
    price_usd: Decimal
    budget_usd: Decimal


class PriceCheck(BaseModel):
    """The price of what is about to be submitted, against the Project's Spend and Budget.

    `prices` has one entry per Request (several for `--count N`), confirmed together.
    """

    prices: list[Decimal]
    total_usd: Decimal
    spend_usd: Decimal
    budget_usd: Decimal | None
    warnings: list[BudgetWarning]


def project_spend(project: Project) -> Decimal:
    """The sum of `estimate_usd` over the Project's completed Requests."""
    return sum((billed_price(record) or Decimal(0) for record in list_requests(project)), Decimal(0))


def billed_price(record: RequestRecord) -> Decimal | None:
    """What a Request adds to Spend: its estimated price once `completed`, else None."""
    if record.status != "completed" or record.estimate_usd is None:
        return None
    try:
        return Decimal(record.estimate_usd)
    except InvalidOperation:  # a hand-edited record must not break every submit
        return None


def check_price(project: Project, prices: Sequence[Decimal]) -> PriceCheck:
    """Check the prices of Requests about to be submitted against the Project's Budget."""
    total = sum(prices, Decimal(0))
    spend = project_spend(project)
    budget = load_config(project).budget
    warnings = []
    if budget is not None and spend + total > budget:
        warnings.append(
            BudgetWarning(
                message=(
                    f"Spend ${spend} plus ${total} goes over the Budget of ${budget} of Project {project.name}"
                ),
                spend_usd=spend,
                price_usd=total,
                budget_usd=budget,
            )
        )
    return PriceCheck(prices=list(prices), total_usd=total, spend_usd=spend, budget_usd=budget, warnings=warnings)
