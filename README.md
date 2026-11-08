# Decision Hub — B2B AI Decision & Automation Hub (MVP)

Deal Desk workflow: a free-text campaign brief becomes validated structured requirements,
a deterministic, policy-flagged quote, and an audit trail. AI suggests fields; code decides prices;
humans decide outcomes. Spec: *Project 01 Master Specification, Draft v1.1*.

**Status: Phase 4 done** — intake → extraction → validation → correction → pricing →
operations feasibility → manager approval → exactly-once final proposal → mock email/CRM → audit.
Real LLM provider (Phase 3) and dashboard/deployment (Phase 5) are next (see `docs/PLAN.md`).

All data is synthetic. Rate card numbers are portfolio fixtures, not real prices.

## Run locally

Needs Python 3.12 + `uv`, Node 20+, PostgreSQL running locally.

```bash
createdb decision_hub && createdb decision_hub_test
cd backend && uv sync && uv run alembic upgrade head && uv run python -m app.seed
uv run uvicorn app.main:app --port 8010
```

```bash
cd frontend && pnpm install && pnpm dev   # http://localhost:5173 (proxies /v1 to :8010)
```

Pick a demo persona in the sidebar: Sales creates briefs; Analyst extracts, corrects, prices and
finalizes; Operations checks feasibility; Manager approves or rejects. Personas sign in through a
server-signed HttpOnly session cookie. Demo mode (password-less persona sign-in) is on by default —
set `DH_DEMO_MODE=false` and `DH_SESSION_SECRET` for anything holding real data.

## Configuration (`backend/.env`, prefix `DH_`)

| Variable | Default | Notes |
|---|---|---|
| `DH_DATABASE_URL` | `postgresql+psycopg:///decision_hub` | |
| `DH_DEMO_MODE` | `true` | Password-less persona sign-in for the demo |
| `DH_SESSION_SECRET` | random per process | Set it, or sessions end on every restart |
| `DH_LLM_PROVIDER` | `mock` | `mock` is a labelled regex parser, not an LLM. Real providers arrive in Phase 3 and read their API key from the environment only. |

## Tests

```bash
cd backend && uv run pytest && uv run ruff check app tests
cd frontend && pnpm typecheck
```

64 backend tests: pricing oracle (hand-computed), validation, extractor, sessions, approvals,
concurrent finalize, outbox fault injection and rate card replay — named after the spec's UAT IDs,
run against a real Postgres test DB through the real migrations.

## Docs
- `docs/PLAN.md` — phases, environment constraints, exit gates
- `docs/ARCHITECTURE.md` — layers, rules the code enforces, pricing formula
- `docs/TRACEABILITY.md` — requirement → code → test, plus decisions and known limits
