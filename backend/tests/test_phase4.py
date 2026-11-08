"""Phase 4: sessions, approvals, finalize, export, outbox, rate card versions.
Test names cite spec IDs (A-FR / A-BR / UAT)."""

import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.auth import hash_password, issue_token
from app.config import get_settings
from app.db import SessionLocal
from app.models import Brief, Notification, Quote, User
from app.services import approvals as appr
from app.services import briefs as svc
from app.services import outbox
from tests.conftest import as_user
from tests.test_requirement_and_extractor import EN

SALES, ANALYST, MANAGER = as_user("ana.sales"), as_user("budi.analyst"), as_user("citra.manager")
OPS, ADMIN, AUDITOR = as_user("dewi.ops"), as_user("eko.admin"), as_user("fajar.auditor")


def priced(client, discount="0", text=EN):
    bid = client.post(
        "/v1/briefs", json={"title": "Acme Q1", "raw_text": text}, headers=SALES
    ).json()["id"]
    client.post(f"/v1/briefs/{bid}/extract", headers=ANALYST)
    q = client.post(f"/v1/briefs/{bid}/quotes", json={"discount_pct": discount}, headers=ANALYST)
    assert q.status_code == 201, q.text
    return bid, q.json()


def decide(client, q, decision="approve", reason=None, who=MANAGER, rev=None):
    return client.post(
        f"/v1/quotes/{q['id']}/decision",
        json={"decision": decision, "reason": reason, "expected_revision": rev or q["revision"]},
        headers=who,
    )


def finalize(client, qid, key="fin-1", who=ANALYST):
    headers = dict(who)
    if key:
        headers["Idempotency-Key"] = key
    return client.post(f"/v1/quotes/{qid}/finalize", headers=headers)


# ------------------------------------------------------------------ sessions (A-NFR-01)


def test_client_chosen_identity_header_is_ignored(client):
    assert client.get("/v1/me", headers={"X-User": "citra.manager"}).status_code == 401


def test_forged_and_expired_tokens_are_rejected(client):
    good = issue_token("budi.analyst")
    body, _ = good.split(".")
    forged = issue_token("citra.manager").split(".")[0] + "." + good.split(".")[1]
    expired = issue_token("budi.analyst", ttl_seconds=-1)
    for tok in (forged, f"{body}.AAAA", expired, "garbage"):
        r = client.get("/v1/me", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401, tok


def test_demo_login_sets_http_only_cookie_session(client):
    r = client.post("/v1/auth/demo-login", json={"user_id": "citra.manager"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/v1/me").json()["role"] == "manager"  # cookie carried by client
    client.post("/v1/auth/logout")
    client.cookies.clear()
    assert client.get("/v1/me").status_code == 401


def test_password_login_and_demo_mode_off(client):
    with SessionLocal() as s:
        s.get(User, "fajar.auditor").password_hash = hash_password("correct horse")
        s.commit()
    bad = client.post("/v1/auth/login", json={"user_id": "fajar.auditor", "password": "nope"})
    assert bad.status_code == 401
    ok = client.post("/v1/auth/login", json={"user_id": "fajar.auditor", "password": "correct horse"})
    assert ok.status_code == 200 and ok.json()["user"]["role"] == "auditor"
    settings = get_settings()
    settings.demo_mode = False
    try:
        assert client.post("/v1/auth/demo-login", json={"user_id": "citra.manager"}).status_code == 404
        assert client.get("/v1/users").status_code == 404
    finally:
        settings.demo_mode = True


# ------------------------------------------------------------------ decisions (A-FR-08)


def test_manager_approves_clean_quote(client):
    bid, q = priced(client)
    r = decide(client, q)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["review"]["approval"]["actor"] == "citra.manager"
    assert client.get(f"/v1/briefs/{bid}", headers=ANALYST).json()["status"] == "approved"


def test_only_managers_decide(client):
    _, q = priced(client)
    for who in (ANALYST, SALES, OPS, ADMIN):
        assert decide(client, q, who=who).status_code in (403, 404)


def test_flagged_quote_needs_reason_and_reject_always_needs_reason(client):
    _, q = priced(client, discount="12")
    assert decide(client, q).json()["detail"]["code"] == "reason_required"
    _, q2 = priced(client)
    assert decide(client, q2, "reject").json()["detail"]["code"] == "reason_required"
    r = decide(client, q, reason="Strategic logo, margin accepted for Q1")
    assert r.status_code == 200 and r.json()["status"] == "approved"


def test_uat_a07_self_approval_is_blocked_and_audited(client):
    """The API's role rules already stop managers from creating quotes, so this test goes
    through the service layer to prove the rule holds on its own."""
    bid, _ = priced(client)
    with SessionLocal() as s:
        manager = s.get(User, "citra.manager")
        brief = s.get(Brief, bid)
        own = svc.create_quote(s, user=manager, brief=brief, discount_pct="0")
        with pytest.raises(svc.WorkflowError) as exc:
            appr.decide(s, user=manager, quote_id=own.id, decision="approve",
                        reason=None, expected_revision=own.revision)
        assert exc.value.code == "self_approval_forbidden" and exc.value.http_status == 403
        s.expire_all()
        assert s.get(Quote, own.id).status == "pending_approval"
    actions = [e["action"] for e in client.get(f"/v1/briefs/{bid}/audit", headers=AUDITOR).json()]
    assert "quote.decision_blocked" in actions and "quote.approved" not in actions


def test_stale_revision_and_superseded_quote_cannot_be_decided(client):
    bid, q1 = priced(client)
    assert decide(client, q1, rev=q1["revision"] + 5).json()["detail"]["code"] == "stale_quote"
    q2 = client.post(f"/v1/briefs/{bid}/quotes", json={"discount_pct": "5"}, headers=ANALYST).json()
    assert client.get(f"/v1/quotes/{q1['id']}", headers=MANAGER).json()["status"] == "superseded"
    assert decide(client, q1).json()["detail"]["code"] == "stale_quote"
    assert decide(client, q2).status_code == 200


def test_uat_a08_edit_after_approval_invalidates_it(client):
    bid, q = priced(client)
    assert decide(client, q).status_code == 200
    client.patch(
        f"/v1/briefs/{bid}/requirements",
        json={"fields": {"audience": "CISOs only"}}, headers=ANALYST,
    )
    assert client.get(f"/v1/quotes/{q['id']}", headers=MANAGER).json()["status"] == "superseded"
    assert finalize(client, q["id"]).json()["detail"]["code"] == "not_approved"
    brief = client.get(f"/v1/briefs/{bid}", headers=ANALYST).json()
    assert brief["status"] == "ready_for_pricing"  # must be re-priced and re-approved


# ------------------------------------------------------------------ feasibility (A-BR-08)

OFF_CATALOG = EN + " Also produce 2 explainer videos."


def test_out_of_catalog_needs_operations_before_approval(client):
    _, q = priced(client, text=OFF_CATALOG)
    assert q["pricing"]["requires_operations_review"]
    assert decide(client, q).json()["detail"]["code"] == "feasibility_pending"
    assert client.post(
        f"/v1/quotes/{q['id']}/feasibility", json={"feasible": True, "note": "fine"}, headers=ANALYST
    ).status_code == 403
    r = client.post(
        f"/v1/quotes/{q['id']}/feasibility",
        json={"feasible": True, "note": "Video team has capacity in January"}, headers=OPS,
    )
    assert r.json()["review"]["feasibility"]["feasible"] is True
    assert decide(client, q).status_code == 200


def test_not_feasible_quote_can_only_be_rejected(client):
    _, q = priced(client, text=OFF_CATALOG)
    client.post(
        f"/v1/quotes/{q['id']}/feasibility",
        json={"feasible": False, "note": "No video capacity this quarter"}, headers=OPS,
    )
    assert decide(client, q).json()["detail"]["code"] == "not_feasible"
    assert decide(client, q, "reject", reason="Ops cannot deliver video").json()["status"] == "rejected"


# ------------------------------------------------------------------ finalize (A-FR-09, A-BR-07)


def test_finalize_requires_key_and_approval(client):
    _, q = priced(client)
    assert finalize(client, q["id"], key=None).status_code == 400
    assert finalize(client, q["id"]).json()["detail"]["code"] == "not_approved"


def test_finalize_is_exactly_once(client):
    bid, q = priced(client)
    decide(client, q)
    first = finalize(client, q["id"], key="k-1")
    assert first.status_code == 200 and first.json()["status"] == "finalized"
    again = finalize(client, q["id"], key="k-1")
    assert again.headers.get("Idempotent-Replay") == "true"
    assert again.json()["review"]["final"]["id"] == first.json()["review"]["final"]["id"]
    assert finalize(client, q["id"], key="k-2").json()["detail"]["code"] == "already_finalized"
    # finalized brief is locked
    r = client.patch(f"/v1/briefs/{bid}/requirements", json={"fields": {"audience": "x y z"}}, headers=ANALYST)
    assert r.json()["detail"]["code"] == "brief_finalized"
    assert client.post(f"/v1/briefs/{bid}/quotes", json={}, headers=ANALYST).status_code == 409


def test_uat_a10_concurrent_finalize_produces_one_final(client):
    _, q = priced(client)
    decide(client, q)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda i: finalize(client, q["id"], key=f"race-{i}"), range(6)))
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) == 1 and codes.count(409) == 5, codes
    with SessionLocal() as s:
        assert s.query(Notification).count() == 2  # one email + one CRM, not 12


def test_uat_a12_exports_cite_revisions_and_final_hash_matches(client):
    _, q = priced(client)
    draft = client.get(f"/v1/quotes/{q['id']}/proposal", headers=ANALYST)
    assert "DRAFT" in draft.text and "rate card 2026.11-v1" in draft.text
    assert f"Quote r{q['revision']}" in draft.text and "requirements r1" in draft.text
    assert draft.headers["content-security-policy"].startswith("default-src 'none'")
    decide(client, q)
    final = finalize(client, q["id"]).json()["review"]["final"]
    doc = client.get(f"/v1/quotes/{q['id']}/proposal", headers=SALES)  # owner may read it
    assert "FINAL" in doc.text and "Approved by citra.manager" in doc.text
    assert hashlib.sha256(doc.text.encode()).hexdigest() == final["document_sha256"]
    assert client.get(f"/v1/quotes/{q['id']}/proposal", headers=as_user("bima.sales")).status_code == 404


def test_proposal_escapes_user_text(client):
    bid = client.post(
        "/v1/briefs",
        json={"title": "<script>alert(1)</script> deal", "raw_text": EN},
        headers=SALES,
    ).json()["id"]
    client.post(f"/v1/briefs/{bid}/extract", headers=ANALYST)
    q = client.post(f"/v1/briefs/{bid}/quotes", json={}, headers=ANALYST).json()
    html = client.get(f"/v1/quotes/{q['id']}/proposal", headers=ANALYST).text
    assert "<script>" not in html and "&lt;script&gt;" in html


# ------------------------------------------------------------------ outbox (A-FR-11/12)


def test_finalize_delivers_email_and_crm_once(client):
    bid, q = priced(client)
    decide(client, q)
    finalize(client, q["id"])
    notes = client.get(f"/v1/briefs/{bid}/notifications", headers=ANALYST).json()
    assert sorted((n["channel"], n["status"]) for n in notes) == [("crm", "sent"), ("email", "sent")]
    assert len(outbox.get_notifier().delivered) == 2


def test_uat_a10_retry_then_fail_then_replay_without_duplicates(client):
    mock = outbox.MockNotifier(fail_next=6)  # 2 notifications × 3 attempts
    outbox.set_notifier(mock)
    bid, q = priced(client)
    decide(client, q)
    finalize(client, q["id"])  # attempt 1 for both: fails
    for _ in range(2):  # attempts 2 and 3: fail -> marked failed
        client.post("/v1/admin/outbox/dispatch", headers=ADMIN)
    notes = client.get(f"/v1/briefs/{bid}/notifications", headers=ANALYST).json()
    assert {(n["status"], n["attempts"]) for n in notes} == {("failed", 3)}
    assert all("timeout" in n["last_error"] for n in notes)
    assert client.post("/v1/admin/outbox/dispatch", headers=ANALYST).status_code == 403
    for n in notes:
        assert client.post(f"/v1/admin/outbox/{n['id']}/requeue", headers=ADMIN).json()["status"] == "pending"
    assert client.post("/v1/admin/outbox/dispatch", headers=ADMIN).json()["sent"] == 2
    assert len(mock.delivered) == 2


def test_crash_after_delivery_is_not_a_duplicate(client):
    class CrashAfterSend(outbox.MockNotifier):
        crash = True

        def deliver(self, channel, key, payload):
            super().deliver(channel, key, payload)
            if self.crash:
                raise RuntimeError("worker died before marking sent")

    mock = CrashAfterSend()
    outbox.set_notifier(mock)
    _, q = priced(client)
    decide(client, q)
    finalize(client, q["id"])
    mock.crash = False
    client.post("/v1/admin/outbox/dispatch", headers=ADMIN)
    assert mock.calls == 4 and len(mock.delivered) == 2  # re-sent, but one effect per key


# ------------------------------------------------------------------ rate cards (A-FR-17, UAT-A15)

V2 = {
    "version": "2027.01-v2",
    "effective_from": "2027-01-01",
    "floor_margin_pct": "35",
    "discount_review_threshold_pct": "10",
    "max_discount_pct": "30",
    "geo_multipliers": {"NA": ["1", "1"], "EMEA": ["1.1", "1.12"], "APAC": ["0.95", "0.92"], "LATAM": ["0.85", "0.82"]},
    "catalog": {
        "content_syndication_lead": {"label": "Content syndication lead", "unit_price": "50", "unit_cost": "27", "monthly_capacity": 5000},
        "mql": {"label": "Marketing qualified lead", "unit_price": "90", "unit_cost": "49", "monthly_capacity": 2000},
    },
}


def test_uat_a15_new_rate_card_keeps_old_quotes_reproducible(client):
    bid, old = priced(client)
    assert client.post("/v1/rate-cards", json=V2, headers=ANALYST).status_code == 403
    assert client.post("/v1/rate-cards", json=V2, headers=ADMIN).status_code == 201
    assert client.post("/v1/rate-cards", json=V2, headers=ADMIN).status_code == 409
    replay = client.get(f"/v1/quotes/{old['id']}/replay", headers=AUDITOR).json()
    assert replay["matches"] and replay["rate_card_version"] == "2026.11-v1"
    new = client.post(f"/v1/briefs/{bid}/quotes", json={}, headers=ANALYST).json()
    # EMEA ×1.10: 1500×50 + 300×90 = 102,000 × 1.1 = 112,200.00
    assert new["rate_card_version"] == "2027.01-v2" and new["list_total"] == "112200.00"
    assert client.get("/v1/rate-cards/active", headers=ANALYST).json()["version"] == "2027.01-v2"


@pytest.mark.parametrize(
    "patch",
    [
        {"geo_multipliers": {"NA": ["1", "1"]}},
        {"catalog": {"Bad Key": {"label": "x y", "unit_price": "1", "unit_cost": "1", "monthly_capacity": 1}}},
        {"catalog": {"mql": {"label": "MQL", "unit_price": "0", "unit_cost": "1", "monthly_capacity": 1}}},
        {"discount_review_threshold_pct": "40"},
    ],
)
def test_invalid_rate_cards_are_refused(client, patch):
    r = client.post("/v1/rate-cards", json={**V2, **patch}, headers=ADMIN)
    assert r.status_code == 422, r.text


# ------------------------------------------------------------------ audit (UAT-A09)


def test_full_history_reconstructs_decision_chain(client):
    bid, q = priced(client, discount="12")
    decide(client, q, reason="Approved for strategic account")
    finalize(client, q["id"], who=MANAGER)
    events = client.get(f"/v1/briefs/{bid}/audit", headers=AUDITOR).json()
    chain = [(e["actor"], e["action"]) for e in events if e["action"].startswith("quote.")]
    assert chain == [
        ("budi.analyst", "quote.priced"),
        ("citra.manager", "quote.approved"),
        ("citra.manager", "quote.finalized"),
    ]
    statuses = [e["after"]["status"] for e in events if e["action"] == "brief.status_changed"]
    assert statuses == ["ready_for_pricing", "priced", "approved", "finalized"]
    assert client.get(f"/v1/briefs/{bid}/audit", headers=SALES).status_code == 403
