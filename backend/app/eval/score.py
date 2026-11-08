"""Pure scoring for the extraction evaluation (A-FR-15, BO-A6, spec §A16).

Per field, each case lands in exactly one bucket:
  tp      expected value present, predicted value equal
  wrong   both present, not equal
  fn      expected present, predicted missing          (missed)
  halluc  expected missing, predicted present          (invented)
  tn      both missing                                  (correctly flagged as missing)

precision = tp / (tp + wrong + halluc)   recall = tp / (tp + wrong + fn)
exact     = (tp + tn) / n                 -> BO-A6 target: >= 90% field-level exact match

Equality rules (documented so results are reproducible):
  - enums exact; geo as a set; budget amount as a 2-dp decimal + currency;
  - deliverables as a multiset of (type, volume) where every non-catalog type counts as one
    "off-catalog" class (models name new items differently; what matters is that they are
    not silently mapped onto a priced catalog item);
  - timeline: start and end dates exact (start may be null);
  - audience: token Jaccard >= 0.5 (it is free text).
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.pricing import RATE_CARD_V1, price_requirement
from app.domain.requirement import REQUIRED_FIELDS, FieldValue, validate_requirement

CATALOG = set(RATE_CARD_V1["catalog"])
_STOP = {"and", "the", "at", "in", "of", "with", "for", "dan", "di", "yang", "a", "an"}


class _Bad(Exception):
    pass


def _empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9+]+", s.lower()) if t not in _STOP and len(t) > 1}


def _canon(field: str, v: Any) -> Any:
    try:
        if field in ("client_sector", "campaign_objective"):
            return str(v).strip().lower()
        if field == "audience":
            return frozenset(_tokens(str(v)))
        if field == "geo":
            return frozenset(str(g).upper() for g in v)
        if field == "deliverables":
            items = []
            for d in v:
                t = str(d["type"]).strip()
                items.append((t if t in CATALOG else "<off_catalog>", int(d["volume"])))
            return tuple(sorted(items))
        if field == "budget":
            return (Decimal(str(v["amount"]).replace(",", "")).quantize(Decimal("0.01")),
                    str(v["currency"]).upper())
        if field == "timeline":
            return (v.get("start_date") or None, v["end_date"])
    except (KeyError, TypeError, ValueError, InvalidOperation, AttributeError) as e:
        raise _Bad from e
    raise _Bad


def field_equal(field: str, expected: Any, got: Any) -> bool:
    try:
        a, b = _canon(field, expected), _canon(field, got)
    except _Bad:
        return False
    if field == "audience":
        if not a or not b:
            return False
        return len(a & b) / len(a | b) >= 0.5
    return a == b


def bucket(field: str, expected: Any, got: Any) -> str:
    e, g = _empty(expected), _empty(got)
    if e and g:
        return "tn"
    if e:
        return "halluc"
    if g:
        return "fn"
    return "tp" if field_equal(field, expected, got) else "wrong"


def _price(normalized: dict[str, Any], received: date) -> tuple[str, list[str]] | None:
    try:
        r = price_requirement(normalized, RATE_CARD_V1, discount_pct=0, reference_date=received)
    except Exception:  # noqa: BLE001 - any failure to price counts as "could not price"
        return None
    return r.net_total, sorted(f.code for f in r.flags)


def score_case(case: dict[str, Any], predicted: dict[str, FieldValue]) -> dict[str, Any]:
    expected = case["expected"]
    buckets = {f: bucket(f, expected.get(f), predicted[f].value) for f in REQUIRED_FIELDS}
    pred_validation = validate_requirement(predicted)

    # Pricing agreement: what the deterministic engine would charge from the expected vs the
    # extracted requirement. A mismatch here is a business error, not just an NLP error.
    pricing_match = None
    if case["expected_validation"] == "valid":
        exp_env = {f: FieldValue(value=expected[f], status="found") for f in REQUIRED_FIELDS}
        exp_norm = validate_requirement(exp_env).normalized
        received = date.fromisoformat(case["received_on"])
        if pred_validation.status == "valid":
            pricing_match = _price(exp_norm, received) == _price(pred_validation.normalized, received)
        else:
            pricing_match = False  # a valid brief that could not be priced is a miss

    unverified = sum(
        1 for f in REQUIRED_FIELDS
        if predicted[f].value is not None and predicted[f].source_quote is None
    )
    return {
        "id": case["id"],
        "category": case["category"],
        "buckets": buckets,
        "validation_expected": case["expected_validation"],
        "validation_got": pred_validation.status,
        "pricing_match": pricing_match,
        "unverified_values": unverified,
        "failures": [
            {"field": f, "outcome": b, "expected": expected.get(f), "got": predicted[f].value,
             "status": predicted[f].status, "note": predicted[f].note}
            for f, b in buckets.items() if b in ("wrong", "fn", "halluc")
        ],
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in results if "buckets" in r]
    per_field: dict[str, Any] = {}
    total_ok = total = 0
    for f in REQUIRED_FIELDS:
        c = Counter(r["buckets"][f] for r in scored)
        n = sum(c.values())
        prec_den = c["tp"] + c["wrong"] + c["halluc"]
        rec_den = c["tp"] + c["wrong"] + c["fn"]
        per_field[f] = {
            "counts": dict(c),
            "precision": round(c["tp"] / prec_den, 4) if prec_den else None,
            "recall": round(c["tp"] / rec_den, 4) if rec_den else None,
            "exact": round((c["tp"] + c["tn"]) / n, 4) if n else None,
        }
        total_ok += c["tp"] + c["tn"]
        total += n

    by_cat: dict[str, list[int]] = {}
    for r in scored:
        ok = sum(1 for b in r["buckets"].values() if b in ("tp", "tn"))
        acc = by_cat.setdefault(r["category"], [0, 0])
        acc[0] += ok
        acc[1] += len(r["buckets"])

    priced = [r["pricing_match"] for r in scored if r["pricing_match"] is not None]
    return {
        "cases": len(results),
        "scored_cases": len(scored),
        "errored_cases": len(results) - len(scored),
        "field_exact_match": round(total_ok / total, 4) if total else None,
        "per_field": per_field,
        "by_category": {k: round(v[0] / v[1], 4) for k, v in sorted(by_cat.items())},
        "validation_agreement": round(
            sum(r["validation_expected"] == r["validation_got"] for r in scored) / len(scored), 4
        ) if scored else None,
        "pricing_mismatch_rate": round(priced.count(False) / len(priced), 4) if priced else None,
        "unverified_values": sum(r["unverified_values"] for r in scored),
    }
