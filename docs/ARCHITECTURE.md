# Architecture

```
frontend (Vite/React/TS, pearl UI)
        │  JSON over HTTP, X-User demo header (Phase 4: real session auth)
        ▼
backend  FastAPI  ── api/        HTTP layer: request/response models, role checks, error mapping
                  ── services/   workflow orchestration, transactions, audit writes
                  ── domain/     PURE code: requirement schema, validation, pricing, rate card
                  ── llm/        provider adapter: mock | anthropic (Phase 3)
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
Phase 4 adds quote states: `draft → needs_review → approved | rejected → finalized`.

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

## Auth (Slice 0)
Demo-only: the `X-User` header names a seeded user; roles are checked per endpoint. This is not
real authentication and is replaced in Phase 4.
