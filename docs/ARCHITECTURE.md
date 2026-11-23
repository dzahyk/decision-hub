# Architecture

```
frontend (Vite/React/TS, pearl UI)
        │  JSON over HTTP, HMAC-signed session in an HttpOnly cookie (Bearer for API clients)
        ▼
backend  FastAPI  ── api/        HTTP layer: request/response models, role checks, error mapping
                  ── services/   workflow orchestration, transactions, audit writes
                  ── domain/     PURE code: requirement schema, validation, pricing, rate card
                  ── llm/        provider adapter: mock (default) | anthropic
        │
        ▼
PostgreSQL  users · briefs · requirement_revisions · rate_cards · quotes · audit_events
```

## Rules the code enforces

1. **LLM proposes, code decides.** The adapter returns field *suggestions* with a source quote per
   field. It never sees the rate card and never writes prices, approvals or policy (A-BR-04).
2. **Pricing is pure.** `domain/pricing.py` takes a requirement + rate card + discount and returns a
   breakdown using `Decimal`. Same inputs → same output (A-NFR-05). No I/O inside.
3. **Raw input is immutable.** `briefs.raw_text` is never updated. Extraction and analyst edits create
   new `requirement_revisions` rows (A-FR-05).
4. **Every state change writes an audit event** in the same DB transaction (actor, action, before, after).
5. **Idempotent create.** `POST /v1/briefs` takes an `Idempotency-Key` header; a repeat returns the
   original brief instead of creating another (A-FR-01, UAT-A01).
6. **No fabricated confidence.** Each field is `found` (with source quote), `inferred` (quote + note)
   or `missing`. No percentage scores (spec p.17).

## Brief status machine

```
received ──extract──► clarification_needed ──analyst edit──► ready_for_pricing ──price──► priced
     └─────extract (complete)──────────────────────────────► ready_for_pricing
```
Quote states: `draft → needs_review → approved | rejected → finalized`.

## Pricing model (simulated rate card v1)

```
line_list  = volume × unit_price × geo_price_multiplier      (rounded HALF_UP, 2 dp)
line_cost  = volume × unit_cost  × geo_cost_multiplier       (rounded HALF_UP, 2 dp)
net_total  = list_total × (1 − discount_pct / 100)           (rounded HALF_UP, 2 dp)
margin_pct = (net_total − cost_total) / net_total × 100      (rounded HALF_UP, 2 dp)
```
Flags: `discount_above_threshold` (>10%), `margin_below_floor`, `over_budget`,
`capacity_exceeded`, `out_of_catalog`. Any of the first two or `out_of_catalog` requires a manager /
ops decision (A-BR-03, A-BR-08); flags are computed server-side and cannot be removed via API.

## Auth
Five business roles (sales, analyst, operations, manager, auditor) plus a seeded `admin`, checked per endpoint.
The admin publishes rate card versions (API only, no screen) and runs or retries notification delivery from the
workflow monitor. Sessions are stateless HMAC-SHA256-signed tokens in an HttpOnly SameSite=Lax cookie (`app/auth.py`); Bearer tokens are also accepted for
API clients. A client-chosen identity header such as `X-User` is ignored. `DH_DEMO_MODE=true` (default) allows
password-less persona sign-in for the portfolio demo; production sets `DH_DEMO_MODE=false` and `DH_SESSION_SECRET`.
See TRACEABILITY decision 5.
