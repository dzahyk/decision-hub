# Implementation Plan — B2B AI Decision & Automation Hub

Source of truth: `Project_01_B2B_AI_Decision_Automation_Hub_Master_Spec.pdf` (Draft v1.1, 08 Nov 2026).
Scope: the BRD MVP only (Deal Desk domain). Property-operations adapter (A-FR-18) is Phase 2 of the *product*, not built here.

## Environment constraints (checked 2026-11-08)

| Item | Found | Consequence |
|---|---|---|
| Python | 3.12 via `uv` | Backend targets 3.12 |
| PostgreSQL | 18.6, Homebrew service, running | Local DBs `decision_hub`, `decision_hub_test` |
| Docker | not installed | No compose for now; native Postgres. Dockerfile added in Phase 5 for deploy |
| Node | 26 / pnpm 11 | Vite + React + TypeScript frontend |
| LLM key | none in env | `LLM_PROVIDER=mock` by default; real provider activates only when a key is set |

## Approach: vertical slice first

Each phase ends with working, tested behaviour — not empty scaffolding.

### Slice 0 (now) — smallest end-to-end path
Brief intake (idempotent) → extraction via provider adapter (mock) → schema validation with
missing-field detection → analyst correction → deterministic pricing on a versioned rate card →
audit events → minimal pearl UI showing source vs extracted vs pricing.
Covers A-FR-01..07, A-FR-10 (partial), UAT-A01..A06.

### Phase 1 — Foundation (folded into Slice 0)
Repo layout, FastAPI app, SQLAlchemy models, Alembic migration, UI tokens.

### Phase 2 — Brief submission & requirements management
Request inbox with filters/sort, brief detail, requirement revisions, clarification status, UX states
(loading / empty / error / recovery). A-FR-01, 02, 05, 13.

### Phase 3 — AI extraction, validation, pricing
Real LLM adapter (Anthropic, provider-agnostic interface), PII redaction before LLM (A-NFR-02),
token cap / timeout / circuit breaker (A-NFR-09), prompt + schema versioning, evaluation set of
50–100 labelled synthetic briefs with field-level precision/recall (A-FR-15, UAT-A13).

### Phase 4 — Approval, audit, roles
Manager approve/reject with reason, self-approval blocked, edit-after-approval invalidates approval,
finalize with idempotency key, export draft (HTML/PDF), audit trail viewer, RBAC on every endpoint.
A-FR-08..12, 14, 17; A-BR-03..07; UAT-A07..A12, A15.

### Phase 5 — Dashboard, QA, performance, deployment
Overview + workflow monitor (lead time, rejections, mismatches, API cost — all reconciled from events,
UAT-A14), fault-injection tests, Playwright E2E, Lighthouse/Core Web Vitals, Dockerfile, deploy.

### Phase 6 — Cinematic landing & case study
Pearl hero (8s loop + poster + reduced motion), product showcase built from real UI recordings,
case study page in sky-portfolio linking to the deployed app. No invented KPI numbers.

## Exit gates (from spec §Execution Control)
- G2: rule oracle + schemas pass tests (end of Slice 0)
- G3: all Must FR/UAT pass, no approval bypass (end of Phase 4)
- G4: real baseline, AI quality, latency, cost, failure cases measured (end of Phase 5)
- G5: responsive live demo, case study, poster/video (end of Phase 6)
