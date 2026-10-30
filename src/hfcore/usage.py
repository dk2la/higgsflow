"""`higgs usage`: what a Project has cost, and the account's usage summary.

The Project part reads only the Request records (ADR 0002): Spend is the estimated
price of its `completed` Requests (`hfcore.spend`). The account part is the analytics
of `GET /models/usage` over the last 30 days. The API has no balance endpoint: the
balance is only in the Console (`CONSOLE_URL`).
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel

from hfcore import clock, naming
from hfcore.api import ApiClient
from hfcore.catalog import Catalog
from hfcore.credentials import load_credentials
from hfcore.errors import HiggsError
from hfcore.projects import Project, load_config
from hfcore.requests import RequestRecord, list_requests, quiet_catalog, request_model
from hfcore.spend import billed_price

CONSOLE_URL = "https://console.higgsfield.ai"
# The account period: the API allows at most 31 days per query; daily buckets fit one page.
ACCOUNT_DAYS = 30
TIMEFRAME = "day"


class ModelSpend(BaseModel):
    """The Spend of a Project's completed Requests to one Model in one Mode."""

    model: str
    mode: str
    requests: int
    spend_usd: Decimal


class ProjectUsage(BaseModel):
    """A Project's Spend, against its Budget when it has one.

    `requests` counts its completed Requests; `remaining_usd` is the Budget minus the
    Spend (negative once over the Budget), None without a Budget. `by_model` splits the
    Spend by Model and Mode, highest Spend first.
    """

    name: str
    spend_usd: Decimal
    requests: int
    budget_usd: Decimal | None
    remaining_usd: Decimal | None
    by_model: list[ModelSpend]


def project_usage(project: Project) -> ProjectUsage:
    """The Project's Spend, from its Request records only."""
    completed = [record for record in list_requests(project) if record.status == "completed"]
    catalog = quiet_catalog()
    groups: dict[tuple[str, str], ModelSpend] = {}
    for record in completed:
        key = _model_and_mode(record, catalog)
        group = groups.setdefault(key, ModelSpend(model=key[0], mode=key[1], requests=0, spend_usd=Decimal(0)))
        group.requests += 1
        group.spend_usd += billed_price(record) or Decimal(0)
    spend = sum((group.spend_usd for group in groups.values()), Decimal(0))
    budget = load_config(project).budget
    return ProjectUsage(
        name=project.name,
        spend_usd=spend,
        requests=len(completed),
        budget_usd=budget,
        remaining_usd=budget - spend if budget is not None else None,
        by_model=sorted(groups.values(), key=lambda group: (-group.spend_usd, group.model, group.mode)),
    )


class ModelUsage(BaseModel):
    """What the account used of one Model (an Endpoint id, as the API names it) in the period."""

    model: str
    unit: str | None
    quantity: Decimal
    cost: Decimal
    currency: str | None


class AccountUsage(BaseModel):
    """The account's usage summary from `GET /models/usage`, summed over the period.

    Analytics, not a billing record: it can lag and differ from the invoice. `cost_usd`
    adds up the USD costs; `by_model` is highest cost first. `raw` is the response body
    when it does not have the documented shape (then `by_model` is empty and `cost_usd`
    None: unknown, not zero), else None.
    """

    start: datetime
    end: datetime
    timeframe: str
    cost_usd: Decimal | None
    by_model: list[ModelUsage]
    raw: Any = None


class AccountUnavailable(BaseModel):
    """The account summary could not be fetched; `error` is the `HiggsError.to_dict()` of why."""

    code: Literal["account_usage_unavailable"] = "account_usage_unavailable"
    message: str
    error: dict[str, Any]


class UsageReport(BaseModel):
    """What `higgs usage` shows. `project` is None outside a Project; `account` is None
    when it could not be fetched (see `warnings`). `balance_url` is where the balance is,
    since the API has no balance endpoint."""

    project: ProjectUsage | None
    account: AccountUsage | None
    balance_url: str = CONSOLE_URL
    warnings: list[AccountUnavailable] = []


def usage_report(project: Project | None) -> UsageReport:
    """The Project's Spend (when in one) and the account's usage summary.

    In a Project, a failing account call (no key, a rejected key, an API or network
    error) becomes a warning, so the Spend is always shown. Outside a Project the account
    summary is all there is, so its error is raised.
    """
    local = project_usage(project) if project is not None else None
    try:
        with ApiClient(load_credentials()) as client:
            account = account_usage(client)
    except HiggsError as error:
        if local is None:
            raise
        warning = AccountUnavailable(
            message=f"The account usage summary is unavailable: {error.message}", error=error.to_dict()
        )
        return UsageReport(project=local, account=None, warnings=[warning])
    return UsageReport(project=local, account=account)


def account_usage(client: ApiClient) -> AccountUsage:
    """The account's usage over the last `ACCOUNT_DAYS` UTC days, up to now."""
    end = clock.now().astimezone(UTC)
    start = datetime.combine(end.date() - timedelta(days=ACCOUNT_DAYS - 1), time(), tzinfo=UTC)
    body = client.usage(start, end, timeframe=TIMEFRAME)
    rows = _usage_rows(body)
    if rows is None:
        return AccountUsage(start=start, end=end, timeframe=TIMEFRAME, cost_usd=None, by_model=[], raw=body)
    groups: dict[tuple[str, str | None, str | None], ModelUsage] = {}
    for row in rows:
        key = (row.model, row.unit, row.currency)
        if key in groups:
            groups[key].quantity += row.quantity
            groups[key].cost += row.cost
        else:
            groups[key] = row
    return AccountUsage(
        start=start,
        end=end,
        timeframe=TIMEFRAME,
        cost_usd=sum((row.cost for row in groups.values() if row.currency in (None, "USD")), Decimal(0)),
        by_model=sorted(groups.values(), key=lambda row: (-row.cost, row.model)),
    )


def _usage_rows(body: Any) -> list[ModelUsage] | None:
    """Every result row of every bucket; None when the body is not the documented shape.

    A malformed row is skipped rather than failing the whole summary.
    """
    if not isinstance(body, dict) or not isinstance(body.get("time_series"), list):
        return None
    rows = []
    for bucket in body["time_series"]:
        results = bucket.get("results") if isinstance(bucket, dict) else None
        for item in results if isinstance(results, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("model"), str):
                continue
            try:
                quantity, cost = Decimal(str(item.get("quantity", 0))), Decimal(str(item.get("cost", 0)))
            except InvalidOperation:
                continue
            unit, currency = item.get("unit"), item.get("currency")
            rows.append(
                ModelUsage(
                    model=item["model"],
                    unit=unit if isinstance(unit, str) else None,
                    quantity=quantity,
                    cost=cost,
                    currency=currency if isinstance(currency, str) else None,
                )
            )
    return rows


def _model_and_mode(record: RequestRecord, catalog: Catalog | None) -> tuple[str, str]:
    """The Model a Request counts towards (the catalog's slug, as `higgs list --model` takes it) and its Mode.

    For an Endpoint the catalog lacks, the model slug of its Generation names, else one
    made from the Endpoint id.
    """
    model = request_model(record, catalog) or (
        record.naming.model_slug if record.naming else naming.path_model_and_mode(record.endpoint)[0]
    )
    mode = record.naming.mode if record.naming else naming.model_and_mode(record.endpoint, catalog)[1]
    return model, mode
