"""Deterministic pricing & feasibility engine (A-FR-06, A-FR-07, A-BR-02, A-BR-03, A-BR-08).

Pure: inputs are a *validated, normalized* requirement + a rate card dict + discount.
No I/O, no clock, no randomness. Same inputs -> identical PricingResult (A-NFR-05).
The LLM never reaches this module.
"""

from __future__ import annotations

import math
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import BaseModel

CENT = Decimal("0.01")

# Simulated, versioned rate card. Numbers are portfolio fixtures, not any company's prices.
RATE_CARD_V1: dict[str, Any] = {
    "version": "2026.11-v1",
    "currency": "USD",
    "floor_margin_pct": "35.00",
    "discount_review_threshold_pct": "10.00",
    "max_discount_pct": "30.00",
    "geo_multipliers": {
        # region: [price multiplier, cost multiplier]
        "NA": ["1.00", "1.00"],
        "EMEA": ["1.10", "1.12"],
        "APAC": ["0.95", "0.92"],
        "LATAM": ["0.85", "0.82"],
    },
    "catalog": {
        "content_syndication_lead": {
            "label": "Content syndication lead",
            "unit_price": "45.00",
            "unit_cost": "26.00",
            "monthly_capacity": 5000,
        },
        "mql": {
            "label": "Marketing qualified lead",
            "unit_price": "85.00",
            "unit_cost": "48.00",
            "monthly_capacity": 2000,
        },
        "intent_qualified_lead": {
            "label": "Intent-qualified lead",
            "unit_price": "120.00",
            "unit_cost": "70.00",
            "monthly_capacity": 1200,
        },
        "appointment_setting": {
            "label": "Booked sales appointment",
            "unit_price": "450.00",
            "unit_cost": "260.00",
            "monthly_capacity": 150,
        },
        "event_registration": {
            "label": "Event / webinar registration",
            "unit_price": "60.00",
            "unit_cost": "34.00",
            "monthly_capacity": 3000,
        },
    },
}


class PricingInputError(ValueError):
    """Raised for inputs the engine refuses to price (e.g. discount above the hard cap)."""


class Flag(BaseModel):
    code: Literal[
        "discount_above_threshold",
        "margin_below_floor",
        "out_of_catalog",
        "capacity_exceeded",
        "over_budget",
    ]
    message: str
    # who must act before the quote can be finalized; "info" blocks nothing
    requires: Literal["manager", "operations", "info"]


class Line(BaseModel):
    type: str
    label: str
    volume: int
    in_catalog: bool
    unit_price: str | None = None
    unit_cost: str | None = None
    price_multiplier: str | None = None
    cost_multiplier: str | None = None
    line_list: str | None = None
    line_cost: str | None = None
    capacity: int | None = None


class PricingResult(BaseModel):
    rate_card_version: str
    currency: str
    campaign_months: int
    blended_region: str
    lines: list[Line]
    list_total: str
    discount_pct: str
    discount_amount: str
    net_total: str
    cost_total: str
    margin_pct: str | None
    flags: list[Flag]
    requires_manager_review: bool
    requires_operations_review: bool


def _d(x: Any) -> Decimal:
    return Decimal(str(x))


def _money(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def campaign_months(start: date | None, end: date, *, reference: date) -> int:
    """Whole 30-day months the campaign spans, minimum 1. `reference` stands in for a
    missing start date so the function stays pure (caller passes the brief's received date)."""
    begin = start or reference
    days = max((end - begin).days, 0)
    return max(1, math.ceil(days / 30))


def price_requirement(
    requirement: dict[str, Any],
    rate_card: dict[str, Any],
    *,
    discount_pct: Decimal | str | int = 0,
    reference_date: date,
) -> PricingResult:
    """Price a normalized requirement (output of validate_requirement().normalized)."""
    discount = _d(discount_pct)
    if discount < 0 or discount > _d(rate_card["max_discount_pct"]):
        raise PricingInputError(
            f"discount must be between 0 and {rate_card['max_discount_pct']}%"
        )

    geos: list[str] = requirement["geo"]
    multipliers = rate_card["geo_multipliers"]
    # Multi-region briefs use the most expensive region's multipliers: conservative and explainable.
    blended = max(geos, key=lambda g: (_d(multipliers[g][0]), g))
    p_mult, c_mult = _d(multipliers[blended][0]), _d(multipliers[blended][1])

    tl = requirement["timeline"]
    months = campaign_months(
        date.fromisoformat(tl["start_date"]) if tl.get("start_date") else None,
        date.fromisoformat(tl["end_date"]),
        reference=reference_date,
    )

    lines: list[Line] = []
    flags: list[Flag] = []
    list_total = cost_total = Decimal(0)
    catalog = rate_card["catalog"]

    for item in requirement["deliverables"]:
        dtype, volume = item["type"], item["volume"]
        entry = catalog.get(dtype)
        if entry is None:
            lines.append(
                Line(type=dtype, label=dtype.replace("_", " "), volume=volume, in_catalog=False)
            )
            flags.append(
                Flag(
                    code="out_of_catalog",
                    message=f"'{dtype}' is not on rate card {rate_card['version']}; "
                    "operations must assess feasibility before approval",
                    requires="operations",
                )
            )
            continue
        line_list = _money(volume * _d(entry["unit_price"]) * p_mult)
        line_cost = _money(volume * _d(entry["unit_cost"]) * c_mult)
        capacity = entry["monthly_capacity"] * months
        list_total += line_list
        cost_total += line_cost
        lines.append(
            Line(
                type=dtype,
                label=entry["label"],
                volume=volume,
                in_catalog=True,
                unit_price=entry["unit_price"],
                unit_cost=entry["unit_cost"],
                price_multiplier=str(p_mult),
                cost_multiplier=str(c_mult),
                line_list=str(line_list),
                line_cost=str(line_cost),
                capacity=capacity,
            )
        )
        if volume > capacity:
            flags.append(
                Flag(
                    code="capacity_exceeded",
                    message=f"{entry['label']}: {volume} requested vs {capacity} capacity "
                    f"over {months} month(s)",
                    requires="operations",
                )
            )

    discount_amount = _money(list_total * discount / 100)
    net_total = list_total - discount_amount
    margin = None
    if net_total > 0:
        margin = _money((net_total - cost_total) / net_total * 100)

    if discount > _d(rate_card["discount_review_threshold_pct"]):
        flags.append(
            Flag(
                code="discount_above_threshold",
                message=f"discount {discount}% exceeds "
                f"{rate_card['discount_review_threshold_pct']}% threshold",
                requires="manager",
            )
        )
    floor = _d(rate_card["floor_margin_pct"])
    if net_total > 0 and margin is not None and margin < floor:
        flags.append(
            Flag(
                code="margin_below_floor",
                message=f"margin {margin}% is below the {floor}% floor",
                requires="manager",
            )
        )

    budget = _d(requirement["budget"]["amount"])
    if net_total > budget:
        flags.append(
            Flag(
                code="over_budget",
                message=f"net price {_money(net_total)} exceeds client budget {_money(budget)}",
                requires="info",
            )
        )

    return PricingResult(
        rate_card_version=rate_card["version"],
        currency=rate_card["currency"],
        campaign_months=months,
        blended_region=blended,
        lines=lines,
        list_total=str(_money(list_total)),
        discount_pct=str(discount.quantize(CENT)),
        discount_amount=str(discount_amount),
        net_total=str(_money(net_total)),
        cost_total=str(_money(cost_total)),
        margin_pct=str(margin) if margin is not None else None,
        flags=flags,
        requires_manager_review=any(f.requires == "manager" for f in flags),
        requires_operations_review=any(f.requires == "operations" for f in flags),
    )
