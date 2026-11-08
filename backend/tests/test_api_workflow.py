"""End-to-end API workflow against a real Postgres test DB. Test names cite spec UAT IDs."""

from tests.conftest import as_user
from tests.test_requirement_and_extractor import EN

ANALYST = as_user("budi.analyst")
SALES = as_user("ana.sales")


def make_brief(client, text=EN, user=SALES, key=None, title="Acme demand gen Q1"):
    headers = dict(user)
    if key:
        headers["Idempotency-Key"] = key
    return client.post("/v1/briefs", json={"title": title, "raw_text": text}, headers=headers)


def audit_actions(client, brief_id):
    r = client.get(f"/v1/audit/{brief_id}", headers=as_user("fajar.auditor"))
    assert r.status_code == 200
    return [e["action"] for e in r.json()]


# ------------------------------------------------------------------ intake


def test_uat_a01_duplicate_request_creates_brief_once(client):
    first = make_brief(client, key="req-001")
    assert first.status_code == 201
    again = make_brief(client, key="req-001")
    assert again.status_code == 200 and again.headers["Idempotent-Replay"] == "true"
    assert again.json()["id"] == first.json()["id"]
    assert len(client.get("/v1/briefs", headers=ANALYST).json()) == 1
    assert audit_actions(client, first.json()["id"]) == ["brief.created"]
    body = first.json()
    assert body["created_by"] == "ana.sales" and body["received_at"] and body["status"] == "received"


def test_uat_a01_same_key_different_payload_conflicts(client):
    make_brief(client, key="req-002")
    r = make_brief(client, key="req-002", text=EN + " Changed.")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "idempotency_conflict"


def test_requests_require_identity_and_validate_input(client):
    assert client.get("/v1/briefs").status_code == 401
    r = client.post("/v1/briefs", json={"title": "x", "raw_text": "short"}, headers=SALES)
    assert r.status_code == 422


# ------------------------------------------------------------------ roles


def test_sales_cannot_run_extraction_or_price(client):
    bid = make_brief(client).json()["id"]
    assert client.post(f"/v1/briefs/{bid}/extract", headers=SALES).status_code == 403
    assert client.post(f"/v1/briefs/{bid}/quotes", json={}, headers=SALES).status_code == 403


def test_sales_only_sees_own_briefs(client):
    bid = make_brief(client).json()["id"]
    other = as_user("bima.sales")
    assert client.get(f"/v1/briefs/{bid}", headers=other).status_code == 404
    assert client.get("/v1/briefs", headers=other).json() == []
    assert client.get(f"/v1/briefs/{bid}", headers=ANALYST).status_code == 200


# ------------------------------------------------------------------ extraction + validation


def test_uat_a02_extraction_produces_valid_versioned_revision(client):
    bid = make_brief(client).json()["id"]
    r = client.post(f"/v1/briefs/{bid}/extract", headers=ANALYST)
    assert r.status_code == 200
    body = r.json()
    rev = body["latest_revision"]
    assert body["status"] == "ready_for_pricing"
    assert (rev["revision"], rev["origin"], rev["provider"]) == (1, "extraction", "mock")
    assert rev["schema_version"] == "requirement.v1" and rev["prompt_version"] == "extract.v1"
    assert rev["validation"]["status"] == "valid"


def test_uat_a03_incomplete_brief_cannot_be_quoted(client):
    bid = make_brief(client, text="Need about 800 webinar registrations for a fintech client.").json()["id"]
    body = client.post(f"/v1/briefs/{bid}/extract", headers=ANALYST).json()
    assert body["status"] == "clarification_needed"
    missing = {i["field"] for i in body["latest_revision"]["validation"]["issues"]}
    assert {"audience", "geo", "budget", "timeline"} <= missing
    r = client.post(f"/v1/briefs/{bid}/quotes", json={}, headers=ANALYST)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "requirements_not_valid"


def test_uat_a04_analyst_correction_keeps_raw_and_adds_revision(client):
    text = "Need about 800 webinar registrations for a fintech client."
    bid = make_brief(client, text=text).json()["id"]
    client.post(f"/v1/briefs/{bid}/extract", headers=ANALYST)
    patch = {
        "audience": "Heads of finance at regional banks",
        "geo": ["APAC"],
        "budget": {"amount": "60000", "currency": "USD"},
        "timeline": {"start_date": "2026-12-01", "end_date": "2027-01-31"},
    }
    r = client.patch(f"/v1/briefs/{bid}/requirements", json={"fields": patch}, headers=ANALYST)
    assert r.status_code == 200
    body = r.json()
    assert body["raw_text"] == text
    assert [x["revision"] for x in body["revisions"]] == [2, 1]
    assert body["revisions"][1]["validation"]["status"] == "incomplete"  # r1 untouched
    latest = body["latest_revision"]
    assert latest["origin"] == "analyst" and latest["validation"]["status"] == "valid"
    assert latest["fields"]["budget"]["status"] == "edited"
    assert latest["fields"]["deliverables"]["status"] == "found"  # carried from r1
    assert body["status"] == "ready_for_pricing"
    assert "requirements.edited" in audit_actions(client, bid)


def test_unknown_field_edit_is_rejected(client):
    bid = make_brief(client).json()["id"]
    r = client.patch(
        f"/v1/briefs/{bid}/requirements", json={"fields": {"discount": 50}}, headers=ANALYST
    )
    assert r.status_code == 422


# ------------------------------------------------------------------ pricing


def _ready_brief(client):
    bid = make_brief(client).json()["id"]
    client.post(f"/v1/briefs/{bid}/extract", headers=ANALYST)
    return bid


def test_uat_a05_quote_matches_oracle_and_records_rate_card_version(client):
    # EN brief: NA+EMEA -> EMEA ×1.10/×1.12 ; 1500 CS + 300 MQL
    # list 74,250 + 28,050 = 102,300.00 ; cost 43,680 + 16,128 = 59,808.00
    # margin 42,492 / 102,300 = 41.54
    bid = _ready_brief(client)
    r = client.post(f"/v1/briefs/{bid}/quotes", json={"discount_pct": "0"}, headers=ANALYST)
    assert r.status_code == 201
    q = r.json()
    assert q["rate_card_version"] == "2026.11-v1"
    assert (q["list_total"], q["net_total"], q["cost_total"], q["margin_pct"]) == (
        "102300.00", "102300.00", "59808.00", "41.54",
    )
    assert q["status"] == "draft" and q["pricing"]["flags"] == []
    assert client.get(f"/v1/briefs/{bid}", headers=ANALYST).json()["status"] == "priced"


def test_uat_a06_discount_over_threshold_requires_manager(client):
    bid = _ready_brief(client)
    q = client.post(
        f"/v1/briefs/{bid}/quotes", json={"discount_pct": "12"}, headers=ANALYST
    ).json()
    assert q["status"] == "needs_review"
    flags = {f["code"]: f["requires"] for f in q["pricing"]["flags"]}
    assert flags["discount_above_threshold"] == "manager"
    assert flags["margin_below_floor"] == "manager"  # 33.56% < 35%


def test_discount_above_hard_cap_is_refused(client):
    bid = _ready_brief(client)
    r = client.post(f"/v1/briefs/{bid}/quotes", json={"discount_pct": "45"}, headers=ANALYST)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_pricing_input"


def test_uat_a09_audit_trail_reconstructs_the_sequence(client):
    bid = _ready_brief(client)
    client.post(f"/v1/briefs/{bid}/quotes", json={"discount_pct": "5"}, headers=ANALYST)
    events = client.get(f"/v1/audit/{bid}", headers=as_user("fajar.auditor")).json()
    assert [(e["actor"], e["action"]) for e in events] == [
        ("ana.sales", "brief.created"),
        ("budi.analyst", "requirements.extracted"),
        ("budi.analyst", "brief.status_changed"),
        ("budi.analyst", "brief.status_changed"),
    ]
    statuses = [e["after"]["status"] for e in events if e["action"] == "brief.status_changed"]
    assert statuses == ["ready_for_pricing", "priced"]
    assert all(e["request_id"] for e in events)
    assert client.get(f"/v1/audit/{bid}", headers=SALES).status_code == 403
