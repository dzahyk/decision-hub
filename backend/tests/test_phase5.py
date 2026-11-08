"""Phase 5: dashboard numbers reconcile with raw logs (UAT-A14), monitor access, perf (A-NFR-04)."""

import importlib.util
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditEvent, Brief, LlmCall, User
from app.services import approvals as appr
from app.services import briefs as svc
from tests.conftest import as_user
from tests.test_phase3 import EchoProvider
from tests.test_phase4 import ANALYST, MANAGER, OPS, SALES, decide, finalize, priced
from tests.test_requirement_and_extractor import EN

AUDITOR = as_user("fajar.auditor")


def _scenario(client):
    # 1) clean quote -> approved -> finalized
    _, q1 = priced(client)
    decide(client, q1)
    finalize(client, q1["id"])
    # 2) flagged quote -> rejected
    _, q2 = priced(client, discount="12")
    decide(client, q2, "reject", reason="Margin too thin for this client")
    # 3) off-catalog quote, ops says feasible, still pending manager
    _, q3 = priced(client, text=EN + " Also produce 2 explainer videos.")
    client.post(f"/v1/quotes/{q3['id']}/feasibility",
                json={"feasible": True, "note": "Studio has capacity"}, headers=OPS)
    # 4) incomplete brief, never priced
    bid4 = client.post("/v1/briefs", json={"title": "Partial", "raw_text": "Need 800 webinar registrations soon."},
                       headers=SALES).json()["id"]
    client.post(f"/v1/briefs/{bid4}/extract", headers=ANALYST)
    # 5) a paid (fake) extraction and a failed one, plus a blocked self-approval
    with SessionLocal() as s:
        analyst, manager = s.get(User, "budi.analyst"), s.get(User, "citra.manager")
        b5, _ = svc.create_brief(s, user=s.get(User, "ana.sales"), title="Paid", raw_text=EN,
                                 source="form", idempotency_key=None)
        svc.run_extraction(s, user=analyst, brief=b5, provider=EchoProvider())
        try:
            svc.run_extraction(s, user=analyst, brief=b5, provider=EchoProvider(fail="timeout"))
        except svc.WorkflowError:
            pass
        own = svc.create_quote(s, user=manager, brief=s.get(Brief, b5.id), discount_pct="0")
        try:
            appr.decide(s, user=manager, quote_id=own.id, decision="approve", reason=None,
                        expected_revision=own.revision)
        except svc.WorkflowError:
            pass


def test_uat_a14_dashboard_reconciles_with_event_logs(client):
    _scenario(client)
    o = client.get("/v1/metrics/overview", headers=MANAGER).json()

    # Recount everything independently from raw rows.
    with SessionLocal() as s:
        events = s.execute(select(AuditEvent.action)).scalars().all()
        calls = s.execute(select(LlmCall)).scalars().all()
        statuses = s.execute(select(Brief.status)).scalars().all()

    assert o["briefs"]["total"] == len(statuses) == 5
    assert o["briefs"]["by_status"] == {st: statuses.count(st) for st in set(statuses)}
    assert o["decisions"]["approved"] == events.count("quote.approved") == 1
    assert o["decisions"]["rejected"] == events.count("quote.rejected") == 1
    assert o["decisions"]["rejection_rate"] == 0.5
    assert o["decisions"]["self_approval_blocked"] == events.count("quote.decision_blocked") == 1
    assert o["extraction"]["calls"] == len(calls)
    assert o["extraction"]["errors"] == {"timeout": 1}
    assert Decimal(o["extraction"]["cost_usd_total"]) == sum(c.cost_usd or 0 for c in calls) == Decimal("0.005")
    assert o["extraction"]["providers"] == {"mock": 4, "anthropic": 2}
    assert o["notifications"] == {"sent": 2, "pending": 0, "failed": 0}
    assert o["lead_time"]["to_final_proposal"]["n"] == events.count("quote.finalized") == 1
    assert o["lead_time"]["to_first_quote"]["n"] == 4  # four briefs reached a priced quote
    assert sum(d["briefs"] for d in o["daily"]) == 5 and len(o["daily"]) == 14
    assert o["eval"] is None or "field_exact_match" in o["eval"]


def test_metrics_and_monitor_roles(client):
    _scenario(client)
    assert client.get("/v1/metrics/overview", headers=SALES).status_code == 403
    assert client.get("/v1/metrics/overview", headers=OPS).status_code == 403
    m = client.get("/v1/monitor", headers=AUDITOR).json()
    assert {"llm_calls", "notifications", "events"} <= set(m)
    assert any(c["error_class"] == "timeout" for c in m["llm_calls"])
    assert all("raw_text" not in c for c in m["llm_calls"])  # monitor never exposes brief text
    assert client.get("/v1/monitor", headers=SALES).status_code == 403


def test_empty_dashboard_has_no_fake_numbers(client):
    o = client.get("/v1/metrics/overview", headers=MANAGER).json()
    assert o["briefs"]["total"] == 0
    assert o["decisions"]["rejection_rate"] is None  # not 0%: nothing was decided
    assert o["extraction"]["error_rate"] is None
    assert o["lead_time"]["to_first_quote"] == {"n": 0, "median_h": None, "p90_h": None}


def test_a_nfr_04_pricing_p95_under_one_second():
    spec = importlib.util.spec_from_file_location(
        "bench", Path(__file__).resolve().parents[1] / "scripts" / "bench_pricing.py"
    )
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    r = bench.run(1000)
    assert r["n"] == 1000 and r["p95_ms"] < 1000
