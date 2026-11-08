"""Proposal document (A-FR-14): self-contained HTML built only from stored, audited data.

Every value is escaped. The document names the quote revision, requirement revision and
rate card version it was computed from, so it can be reconciled with the audit trail (UAT-A12).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from html import escape
from typing import Any

from app.models import Approval, Brief, Quote, RequirementRevision

_LABELS = {
    "client_sector": "Client sector",
    "campaign_objective": "Objective",
    "audience": "Audience",
    "geo": "Regions",
    "budget": "Client budget",
    "timeline": "Timeline",
}


def _money(v: Any, currency: str = "USD") -> str:
    return f"{currency} {Decimal(str(v)):,.2f}"


def _fmt(name: str, v: Any) -> str:
    if name in ("client_sector", "campaign_objective"):
        return str(v).replace("_", " ").capitalize()
    if name == "geo":
        return ", ".join(v)
    if name == "budget":
        return _money(v["amount"], v["currency"])
    if name == "timeline":
        return f"{v['start_date']} to {v['end_date']}" if v.get("start_date") else f"by {v['end_date']}"
    return str(v)


def render_proposal(
    *,
    brief: Brief,
    quote: Quote,
    requirement: RequirementRevision,
    approval: Approval | None,
    final: bool,
    finalized_at: datetime | None = None,
    finalized_by: str | None = None,
) -> str:
    p = quote.pricing
    cur = p["currency"]
    req = requirement.validation["normalized"]
    e = escape

    rows = "".join(
        f"<tr><th>{e(label)}</th><td>{e(_fmt(k, req[k]))}</td></tr>"
        for k, label in _LABELS.items()
        if k in req
    )
    lines = "".join(
        "<tr>"
        f"<td>{e(ln['label'])}{'' if ln['in_catalog'] else ' <em>(not on rate card — not priced)</em>'}</td>"
        f"<td class=n>{ln['volume']:,}</td>"
        f"<td class=n>{e(_money(ln['line_list'], cur)) if ln['line_list'] else '—'}</td>"
        "</tr>"
        for ln in p["lines"]
    )
    flags = "".join(
        f"<li><strong>{e(f['requires'])}</strong>: {e(f['message'])}</li>" for f in p["flags"]
    ) or "<li>None</li>"

    if approval:
        decision = (
            f"Approved by {e(approval.actor)} on {e(approval.at.isoformat(timespec='seconds'))}"
            + (f" — “{e(approval.reason)}”" if approval.reason else "")
        )
    else:
        decision = "Not approved"

    banner = (
        f"<p class=final>FINAL · issued {e(finalized_at.isoformat(timespec='seconds'))} by {e(finalized_by or '')}</p>"
        if final and finalized_at
        else "<p class=draft>DRAFT — not a commercial offer until approved and finalized</p>"
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Proposal — {e(brief.title)} (quote r{quote.revision})</title>
<style>
body{{font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;color:#172335;background:#f7f8fb;margin:0;padding:32px 16px}}
main{{max-width:760px;margin:0 auto;background:#fff;border:1px solid #e6e9f2;border-radius:12px;padding:32px}}
h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:15px;margin:28px 0 8px}}
table{{width:100%;border-collapse:collapse}} th,td{{text-align:left;padding:6px 0;border-bottom:1px solid #eef0f6;vertical-align:top}}
th{{width:34%;color:#6b7489;font-weight:500}} .n{{text-align:right;font-variant-numeric:tabular-nums}}
.draft{{background:#fdf3dd;color:#8a5a00;padding:8px 12px;border-radius:8px;font-weight:600}}
.final{{background:#e7f5ee;color:#1f7a4d;padding:8px 12px;border-radius:8px;font-weight:600}}
.meta{{color:#6b7489;font-size:12px}} .total td{{font-weight:600;font-size:16px}}
</style></head><body><main>
{banner}
<h1>{e(brief.title)}</h1>
<p class=meta>Quote r{quote.revision} · requirements r{requirement.revision} · rate card {e(quote.rate_card_version)} · request {e(str(brief.id))}</p>
<h2>Campaign requirements</h2><table>{rows}</table>
<h2>Deliverables &amp; price</h2>
<table><thead><tr><th>Deliverable</th><th class=n>Volume</th><th class=n>Amount</th></tr></thead><tbody>{lines}</tbody></table>
<table>
<tr><th>List total</th><td class=n>{e(_money(quote.list_total, cur))}</td></tr>
<tr><th>Discount ({e(str(quote.discount_pct))}%)</th><td class=n>−{e(_money(p['discount_amount'], cur))}</td></tr>
<tr class=total><th>Net price</th><td class=n>{e(_money(quote.net_total, cur))}</td></tr>
</table>
<p class=meta>{e(p['blended_region'])} regional rates · {p['campaign_months']} campaign month(s). Prices computed by deterministic rules, not by AI.</p>
<h2>Policy review</h2><ul>{flags}</ul>
<h2>Decision</h2><p>{decision}</p>
<p class=meta>Simulated portfolio scenario. Synthetic data; not an offer from any real company.</p>
</main></body></html>
"""
