"""Pricing oracle tests (A-FR-06/07, UAT-A05/A06, BO-A2).

Expected numbers were computed by hand from rate card 2026.11-v1 — see comments.
"""

from datetime import date

import pytest

from app.domain.pricing import RATE_CARD_V1, PricingInputError, price_requirement

REF = date(2026, 11, 8)


def req(deliverables, geo=("NA",), budget="100000.00", start=None, end="2026-12-31"):
    return {
        "client_sector": "technology",
        "campaign_objective": "lead_generation",
        "audience": "IT directors",
        "geo": list(geo),
        "deliverables": [{"type": t, "volume": v} for t, v in deliverables],
        "budget": {"amount": budget, "currency": "USD"},
        "timeline": {"start_date": start, "end_date": end},
    }


def codes(result):
    return [f.code for f in result.flags]


def test_simple_na_quote_matches_oracle():
    # 1000 MQL × 85.00 × 1.00 = 85,000.00 ; cost 1000 × 48.00 = 48,000.00
    # margin 37,000 / 85,000 = 43.529% -> 43.53 ; 51 days -> 2 months, capacity 4000
    r = price_requirement(
        req([("mql", 1000)], budget="90000", start="2026-11-10"), RATE_CARD_V1,
        discount_pct=0, reference_date=REF,
    )
    assert (r.list_total, r.net_total, r.cost_total, r.margin_pct) == (
        "85000.00", "85000.00", "48000.00", "43.53",
    )
    assert r.campaign_months == 2 and r.lines[0].capacity == 4000
    assert r.flags == [] and not r.requires_manager_review


def test_multi_region_uses_highest_multiplier_and_flags_capacity_and_budget():
    # APAC (0.95/0.92) beats LATAM (0.85/0.82)
    # 500 appt × 450 × 0.95 = 213,750.00 ; cost 500 × 260 × 0.92 = 119,600.00
    # 10% discount = 21,375.00 -> net 192,375.00 ; margin 72,775 / 192,375 = 37.83
    # 2027-01-01..03-31 = 89 days -> 3 months -> capacity 450 < 500
    r = price_requirement(
        req([("appointment_setting", 500)], geo=("LATAM", "APAC"), budget="150000",
            start="2027-01-01", end="2027-03-31"),
        RATE_CARD_V1, discount_pct=10, reference_date=REF,
    )
    assert r.blended_region == "APAC"
    assert (r.list_total, r.discount_amount, r.net_total, r.cost_total, r.margin_pct) == (
        "213750.00", "21375.00", "192375.00", "119600.00", "37.83",
    )
    assert codes(r) == ["capacity_exceeded", "over_budget"]
    # exactly 10% is NOT above the threshold
    assert not r.requires_manager_review and r.requires_operations_review


def test_discount_just_over_threshold_and_margin_floor_need_manager():
    # EMEA 1000 CS × 45 × 1.10 = 49,500.00 ; cost 1000 × 26 × 1.12 = 29,120.00
    # 10.01% = 4,954.95 -> net 44,545.05 ; margin 15,425.05 / 44,545.05 = 34.63 < 35
    r = price_requirement(
        req([("content_syndication_lead", 1000)], geo=("EMEA",)), RATE_CARD_V1,
        discount_pct="10.01", reference_date=REF,
    )
    assert (r.discount_amount, r.net_total, r.margin_pct) == ("4954.95", "44545.05", "34.63")
    assert codes(r) == ["discount_above_threshold", "margin_below_floor"]
    assert r.requires_manager_review


def test_out_of_catalog_item_is_flagged_not_priced():
    # 100 MQL = 8,500.00 / cost 4,800.00 ; the video line is excluded from totals
    r = price_requirement(
        req([("mql", 100), ("video_production", 3)]), RATE_CARD_V1,
        discount_pct=0, reference_date=REF,
    )
    assert r.list_total == "8500.00" and r.cost_total == "4800.00"
    assert [ln.in_catalog for ln in r.lines] == [True, False]
    assert r.lines[1].line_list is None
    assert codes(r) == ["out_of_catalog"] and r.requires_operations_review


def test_missing_start_date_counts_from_reference_date():
    # 2026-11-08 -> 2027-02-05 = 89 days -> 3 months
    r = price_requirement(
        req([("mql", 10)], end="2027-02-05"), RATE_CARD_V1, discount_pct=0, reference_date=REF
    )
    assert r.campaign_months == 3


@pytest.mark.parametrize("bad", ["-1", "30.01", "100"])
def test_discount_outside_hard_cap_is_refused(bad):
    with pytest.raises(PricingInputError):
        price_requirement(
            req([("mql", 10)]), RATE_CARD_V1, discount_pct=bad, reference_date=REF
        )


def test_pricing_is_deterministic():
    """A-NFR-05: same inputs replay to an identical result."""
    args = (req([("mql", 333), ("event_registration", 777)], geo=("EMEA", "NA")), RATE_CARD_V1)
    first = price_requirement(*args, discount_pct="7.5", reference_date=REF).model_dump()
    for _ in range(50):
        assert price_requirement(*args, discount_pct="7.5", reference_date=REF).model_dump() == first
