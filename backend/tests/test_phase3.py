"""Phase 3: PII redaction, Claude adapter (with a fake client — no network, no cost),
service integration (redaction, provenance, call logging, spend guard) and eval scoring."""

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.domain.requirement import FieldValue
from app.eval.score import bucket, field_equal
from app.llm.base import ExtractionError, ExtractionOutput
from app.llm.claude import (
    FALLBACK_BETA,
    OUTPUT_SCHEMA,
    CircuitBreaker,
    ClaudeExtractor,
    cost_usd,
)
from app.llm.mock import MockRulesExtractor
from app.llm.redact import redact, restore
from app.models import Brief, LlmCall, User
from app.services import briefs as svc

RECEIVED = date(2026, 11, 8)

# ------------------------------------------------------------------ redaction (A-NFR-02)


def test_redaction_removes_pii_and_restores_exactly():
    text = ("Contact: Jane Doe (jane.doe@acme.test, +62 812-3456-7890). PIC: Budi Santoso. "
            "NIK 3174012345678901. Card 4111 1111 1111 1111. Call 021 5550 1234.")
    r = redact(text)
    for secret in ("Jane Doe", "jane.doe@acme.test", "+62 812-3456-7890", "Budi Santoso",
                   "3174012345678901", "4111 1111 1111 1111", "021 5550 1234"):
        assert secret not in r.text, secret
    assert r.counts == {"EMAIL": 1, "NIK": 1, "CARD": 1, "PHONE": 2, "PERSON": 2}
    assert restore(r.text, r.mapping) == text


@pytest.mark.parametrize("text", [
    "Budget is $120,000 for 1,500 MQLs.",
    "Runs 2026-12-01 to 2027-02-28.",
    "Anggaran Rp 1.500.000.000 untuk 2.000 leads.",
    "volume 1500000 leads",
])
def test_redaction_keeps_business_numbers(text):
    assert redact(text).text == text


def test_same_value_gets_same_placeholder():
    r = redact("Email a@x.test then a@x.test again")
    assert r.text == "Email [EMAIL_1] then [EMAIL_1] again"


# ------------------------------------------------------------------ Claude adapter (fake client)


def _field(value, status="found", quote=None, note=None):
    return {"value": value, "status": status, "source_quote": quote, "note": note}


GOOD = {
    "client_sector": _field("technology", "inferred", "SaaS company", "SaaS -> technology"),
    "campaign_objective": _field("lead_generation", "found", "lead gen"),
    "audience": _field("IT directors", "found", "IT directors"),
    "geo": _field(["NA"], "found", "North America"),
    "deliverables": _field([{"type": "mql", "volume": 500}], "found", "500 MQLs"),
    "budget": _field({"amount": "60000", "currency": "USD"}, "found", "$60k"),
    "timeline": _field({"start_date": None, "end_date": "2027-03-31"}, "found", "this quote is invented"),
}
BRIEF = "SaaS company, lead gen for IT directors in North America. 500 MQLs, $60k, by 2027-03-31."


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def fake_client(*responses):
    msgs = FakeMessages(responses)
    return SimpleNamespace(beta=SimpleNamespace(messages=msgs)), msgs


def resp(payload, stop="end_turn", model="claude-opus-5-5", inp=1000, out=500):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        stop_reason=stop, model=model, _request_id="req_test",
        usage=SimpleNamespace(input_tokens=inp, output_tokens=out, cache_read_input_tokens=0,
                              cache_creation_input_tokens=0, iterations=None),
    )


def test_request_shape_is_safe_and_versioned():
    client, msgs = fake_client(resp(GOOD))
    out = ClaudeExtractor(client=client, breaker=CircuitBreaker()).extract(BRIEF, received_on=RECEIVED)
    call = msgs.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"]["format"] == {"type": "json_schema", "schema": OUTPUT_SCHEMA}
    assert call["output_config"]["effort"] == get_settings().llm_effort
    assert call["max_tokens"] == get_settings().llm_max_tokens
    assert "untrusted" in call["system"] and "<brief>" in call["messages"][0]["content"]
    assert "2026-11-08" in call["messages"][0]["content"]
    assert "temperature" not in call  # rejected by current models
    # schema has no price/discount/approval fields (A-BR-04)
    assert set(OUTPUT_SCHEMA["properties"]) == set(GOOD) and OUTPUT_SCHEMA["additionalProperties"] is False
    assert out.provider == "anthropic" and out.model_version == "claude-opus-5-5"
    assert out.prompt_version == "extract.claude.v1" and out.provider_request_id == "req_test"
    # opus 5.5: 1000 × $4/M + 500 × $20/M = 0.004 + 0.010
    assert out.cost_usd == Decimal("0.014000")


def test_unverifiable_quote_is_downgraded_not_trusted():
    client, _ = fake_client(resp(GOOD))
    out = ClaudeExtractor(client=client, breaker=CircuitBreaker()).extract(BRIEF, received_on=RECEIVED)
    assert out.fields["budget"].status == "found" and out.fields["budget"].source_quote == "$60k"
    tl = out.fields["timeline"]
    assert tl.status == "inferred" and tl.source_quote is None and "not found" in tl.note


@pytest.mark.parametrize(("response", "error_class"), [
    (resp(GOOD, stop="refusal"), "refusal"),
    (resp(GOOD, stop="max_tokens"), "truncated"),
    (resp("{not json"), "malformed_output"),
    (resp({"client_sector": _field("technology")}), "malformed_output"),
])
def test_bad_responses_raise_with_usage_kept(response, error_class):
    client, _ = fake_client(response)
    with pytest.raises(ExtractionError) as exc:
        ClaudeExtractor(client=client, breaker=CircuitBreaker()).extract(BRIEF, received_on=RECEIVED)
    assert exc.value.error_class == error_class
    assert exc.value.meta["cost_usd"] == Decimal("0.014000")  # billed calls are still accounted


REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize(("error", "error_class"), [
    (anthropic.APITimeoutError(request=REQ), "timeout"),
    (anthropic.APIConnectionError(request=REQ), "network"),
    (anthropic.RateLimitError("slow down", response=httpx2.Response(429, request=REQ), body=None), "rate_limited"),
    (anthropic.AuthenticationError("bad key", response=httpx2.Response(401, request=REQ), body=None), "auth"),
    (anthropic.InternalServerError("boom", response=httpx2.Response(500, request=REQ), body=None), "http_500"),
])
def test_sdk_errors_map_to_stable_classes(error, error_class):
    client, _ = fake_client(error)
    with pytest.raises(ExtractionError) as exc:
        ClaudeExtractor(client=client, breaker=CircuitBreaker()).extract(BRIEF, received_on=RECEIVED)
    assert exc.value.error_class == error_class


def test_circuit_breaker_opens_then_half_opens():
    now = [0.0]
    breaker = CircuitBreaker(threshold=3, cooldown_s=60, clock=lambda: now[0])
    errors = [anthropic.APITimeoutError(request=REQ) for _ in range(3)]
    client, msgs = fake_client(*errors, resp(GOOD))
    ext = ClaudeExtractor(client=client, breaker=breaker)
    for _ in range(3):
        with pytest.raises(ExtractionError):
            ext.extract(BRIEF, received_on=RECEIVED)
    with pytest.raises(ExtractionError) as exc:
        ext.extract(BRIEF, received_on=RECEIVED)
    assert exc.value.error_class == "circuit_open" and len(msgs.calls) == 3  # no 4th API call
    now[0] = 61
    assert ext.extract(BRIEF, received_on=RECEIVED).provider == "anthropic"
    assert breaker.failures == 0


def test_cost_uses_fallback_iterations_when_present():
    usage = SimpleNamespace(iterations=[
        SimpleNamespace(model="claude-opus-5-5", input_tokens=1000, output_tokens=0,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0),
        SimpleNamespace(model="claude-opus-4-8", input_tokens=1000, output_tokens=1000,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0),
    ])
    # 0.004 + (0.005 + 0.025)
    assert cost_usd("claude-opus-5-5", usage) == Decimal("0.034000")
    assert cost_usd("some-unknown-model", SimpleNamespace(input_tokens=1, output_tokens=1)) is None


# ------------------------------------------------------------------ service integration


class EchoProvider:
    """Records what it was sent and quotes back whatever the brief contained."""

    name = "anthropic"
    model = "fake-model"

    def __init__(self, fail: str | None = None):
        self.seen: list[str] = []
        self.fail = fail

    def extract(self, raw_text, *, received_on):
        self.seen.append(raw_text)
        if self.fail:
            raise ExtractionError("nope", error_class=self.fail,
                                  meta={"cost_usd": Decimal("0.002"), "input_tokens": 10})
        out = MockRulesExtractor().extract(raw_text, received_on=received_on)
        # a quote that contains a placeholder must come back restored
        out.fields["audience"] = FieldValue(
            value="IT directors", status="found", source_quote=raw_text.split(".")[0]
        )
        out.fields["geo"] = FieldValue(value=["NA"], status="found", source_quote="made up quote")
        return ExtractionOutput(fields=out.fields, provider=self.name, model_version="fake-model",
                                prompt_version="test.v1", input_tokens=100, output_tokens=50,
                                cost_usd=Decimal("0.0030"), provider_request_id="req_fake")


PII_BRIEF = ("Contact: Jane Doe (jane.doe@acme.test). SaaS client doing lead gen. Target audience: "
             "IT directors. North America. 500 MQLs. Budget $60k. Runs 2027-01-04 to 2027-03-31.")


def _brief(s, text=PII_BRIEF):
    b, _ = svc.create_brief(s, user=s.get(User, "ana.sales"), title="PII test", raw_text=text,
                            source="form", idempotency_key=None)
    return b


def test_service_redacts_restores_verifies_and_logs():
    p = EchoProvider()
    with SessionLocal() as s:
        brief = _brief(s)
        rev = svc.run_extraction(s, user=s.get(User, "budi.analyst"), brief=brief, provider=p)
        assert "Jane Doe" not in p.seen[0] and "jane.doe@acme.test" not in p.seen[0]
        assert rev.fields["audience"]["source_quote"].startswith("Contact: Jane Doe")  # restored
        assert rev.fields["audience"]["source_quote"] in PII_BRIEF
        geo = rev.fields["geo"]
        assert geo["status"] == "inferred" and geo["source_quote"] is None  # invented quote dropped
        call = s.get(LlmCall, rev.llm_call_id)
        assert call.status == "ok" and call.cost_usd == Decimal("0.003000")
        assert call.pii_redacted == {"PERSON": 1, "EMAIL": 1} and call.provider_request_id == "req_fake"


def test_failed_call_is_logged_with_cost_and_audited():
    p = EchoProvider(fail="timeout")
    with SessionLocal() as s:
        brief = _brief(s)
        with pytest.raises(svc.WorkflowError) as exc:
            svc.run_extraction(s, user=s.get(User, "budi.analyst"), brief=brief, provider=p)
        assert exc.value.http_status == 502
        calls = s.query(LlmCall).filter_by(brief_id=brief.id).all()
        assert [(c.status, c.error_class, c.cost_usd) for c in calls] == [
            ("error", "timeout", Decimal("0.002000"))
        ]
        assert s.get(Brief, brief.id).status == "received"


def test_daily_budget_guard_blocks_paid_extraction():
    settings = get_settings()
    old = settings.llm_daily_budget_usd
    settings.llm_daily_budget_usd = 0.004
    try:
        with SessionLocal() as s:
            brief = _brief(s)
            analyst = s.get(User, "budi.analyst")
            svc.run_extraction(s, user=analyst, brief=brief, provider=EchoProvider())  # $0.003
            svc.run_extraction(s, user=analyst, brief=brief, provider=EchoProvider())  # $0.006
            with pytest.raises(svc.WorkflowError) as exc:
                svc.run_extraction(s, user=analyst, brief=brief, provider=EchoProvider())
            assert exc.value.code == "llm_budget_exhausted" and exc.value.http_status == 429
            # mock extraction is free and never blocked
            svc.run_extraction(s, user=analyst, brief=brief, provider=MockRulesExtractor())
    finally:
        settings.llm_daily_budget_usd = old


def test_default_provider_is_mock(client):
    assert client.get("/v1/health").json()["llm_provider"] == "mock"


# ------------------------------------------------------------------ eval scoring


@pytest.mark.parametrize(("field", "expected", "got", "result"), [
    ("geo", ["NA", "EMEA"], ["EMEA", "NA"], "tp"),
    ("budget", {"amount": "60000.00", "currency": "USD"}, {"amount": "60000", "currency": "usd"}, "tp"),
    ("budget", {"amount": "60000.00", "currency": "USD"}, {"amount": "80000", "currency": "USD"}, "wrong"),
    ("deliverables", None, [{"type": "mql", "volume": 500}], "halluc"),
    ("deliverables", [{"type": "video_production", "volume": 3}], [{"type": "explainer_video", "volume": 3}], "tp"),
    ("deliverables", [{"type": "video_production", "volume": 3}], [{"type": "mql", "volume": 3}], "wrong"),
    ("audience", "IT directors at mid-market companies", None, "fn"),
    ("timeline", None, None, "tn"),
])
def test_bucket_rules(field, expected, got, result):
    assert bucket(field, expected, got) == result


def test_audience_uses_token_overlap():
    assert field_equal("audience", "CFOs and finance directors", "finance directors and CFOs")
    assert not field_equal("audience", "CFOs and finance directors", "HR leaders")


def test_eval_runs_end_to_end_on_mock(tmp_path, monkeypatch):
    from app.eval import run as runner

    monkeypatch.setattr(runner, "REPORTS", tmp_path)
    rep = runner.run("mock", runner.load_cases(None, None))
    s = rep["summary"]
    assert s["cases"] == 60 and s["errored_cases"] == 0
    assert s["pii_leaks"] == []  # nothing personal reaches any provider
    assert 0 <= s["field_exact_match"] <= 1 and s["pricing_mismatch_rate"] is not None
    assert set(s["per_field"]) == set(GOOD)
