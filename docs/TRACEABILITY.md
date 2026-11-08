# Requirement traceability — after Phase 5 (2026-11-08)

Evidence = automated test name (101 backend tests + Playwright E2E) unless marked "browser" or "eval".

| Req | What | Code | Evidence | State |
|---|---|---|---|---|
| A-FR-01/02 | Idempotent intake; raw text, source, time, owner | `services/briefs.create_brief` | `test_uat_a01_*` | Done |
| A-FR-03 | Versioned extraction with provenance | `llm/claude.py`, `llm/mock.py`, `run_extraction` | `test_uat_a02_*`, `test_request_shape_is_safe_and_versioned`, `test_unverifiable_quote_is_downgraded_not_trusted` | Done; Claude adapter tested with a fake client, no live run yet |
| A-FR-04 | Schema validation | `domain/requirement.py` | `test_malformed_values…`, `test_uat_a03_*` | Done |
| A-FR-05 | Analyst correction; raw untouched | `edit_requirements` | `test_uat_a04_*` | Done |
| A-FR-06/07 | Deterministic pricing + policy flags | `domain/pricing.py` | pricing oracle tests, `test_uat_a06_*` | Done |
| A-FR-08 | Manager approve/reject with reason; self-approval blocked | `services/approvals.decide` | `test_flagged_quote_needs_reason…`, `test_uat_a07_*` | Done |
| A-FR-09 | Final output only from approved revision | `approvals.finalize` | `test_finalize_requires_key_and_approval`, `test_uat_a08_*` | Done |
| A-FR-10 | Audit: actor, action, before/after, time, request id | `briefs.audit`, `/briefs/{id}/audit` | `test_full_history_reconstructs…`, `test_uat_a09_*` | Done |
| A-FR-11 | Mock CRM/email with retry + idempotency key | `services/outbox.py` | `test_finalize_delivers…`, `test_uat_a10_retry…` | Done |
| A-FR-12 | Replay failed delivery from checkpoint, no duplicate effect | `outbox.requeue`, receiver de-dup | `test_uat_a10_retry…`, `test_crash_after_delivery…` | Done |
| A-FR-13 | UI: status, reasons, next step, history | `Detail.tsx`, `Approvals.tsx` | browser 2026-11-08 | Done |
| A-FR-14 | Export proposal citing quote/requirement/rate card versions | `proposal.py`, `/quotes/{id}/proposal` | `test_uat_a12_*`, `test_proposal_escapes_user_text` | Done (HTML; no PDF) |
| A-FR-17 | Admin publishes rate card versions; old quotes replay | `approvals.publish_rate_card`, `/quotes/{id}/replay` | `test_uat_a15_*`, `test_invalid_rate_cards…` | Done (API only, no admin UI) |
| A-BR-01..03 | Required fields; version stored; flags need manager | see above | see above | Done |
| A-BR-04 | LLM can't touch rates/approvals | adapter returns schema fields only | `test_instructions_inside_the_brief…` | Done for mock |
| A-BR-05 | No self-approval | `decide` checks quote + brief creator | `test_uat_a07_*` (service level) | Done |
| A-BR-06 | Change after approval → new revision, approval void | `supersede_open_quotes` | `test_uat_a08_*`, `test_stale_revision…` | Done |
| A-BR-07 | Exactly one final per quote / key | unique constraints + row locks | `test_finalize_is_exactly_once`, `test_uat_a10_concurrent…` (10/10 runs) | Done |
| A-BR-08 | Off-catalog → operations note before approval | `add_feasibility_note`, `decide` | `test_out_of_catalog…`, `test_not_feasible…` | Done |
| A-NFR-01 | RBAC; identity from server-signed session | `auth.py`, `current_user`, `require` | `test_client_chosen_identity_header_is_ignored`, `test_forged_and_expired…`, role tests | Done (demo mode on by default) |
| A-NFR-03 | Idempotency, transactions, retry policy | outbox, locks | see A-FR-11/12, A-BR-07 | Done |
| A-FR-15 | Repeatable benchmark (≥50 cases) with prompt/model version and error report | `eval/cases.jsonl` (60), `app/eval/` | `test_eval_runs_end_to_end_on_mock`, `eval/reports/*` | Done (mock baseline stored; Claude run pending approval) |
| A-NFR-02 | PII redacted before any LLM call | `llm/redact.py`, `run_extraction` | `test_redaction_*`, `test_service_redacts_restores_verifies_and_logs`, eval `pii_leaks = 0` | Done (cue-based names only) |
| A-NFR-06 | Request id, latency, tokens, cost, error class recorded | `models.LlmCall`, `_record_call` | `test_service_redacts…`, `test_failed_call_is_logged…` | Done |
| A-NFR-09 | Token cap, timeout, circuit breaker, spend guard | `llm/claude.py`, `_check_daily_budget` | `test_circuit_breaker…`, `test_daily_budget_guard…`, `test_sdk_errors_map…` | Done |
| A-FR-16 | Dashboard: lead time, rejection rate, pricing mismatch, API cost | `services/metrics.py`, `/v1/metrics/overview`, `Overview.tsx` | `test_uat_a14_dashboard_reconciles_with_event_logs`, E2E overview test | Done (pricing mismatch comes from the latest eval report) |
| A-NFR-04 | p95 rule calculation < 1 s on 1,000 fixtures | `scripts/bench_pricing.py` | `test_a_nfr_04_pricing_p95_under_one_second`; local run p95 0.014 ms | Done |
| A-NFR-07 | CI runs unit / integration / UAT, build fails on test failure | `.github/workflows/ci.yml` | not yet run on GitHub (no remote) | Written, unverified |
| A-NFR-08 | Accessibility: keyboard, labels, not colour-only, contrast | chips with glyphs, focusable chart marks, table fallbacks | axe (WCAG 2.2 AA, no serious/critical issues) in E2E; Lighthouse a11y 100 | Done for audited pages |
| UAT end-to-end | Full journey in a real browser | `frontend/e2e/` (Playwright 1.62.1) | 5/5 passing locally, 3 consecutive runs (2026-11-08) | Done |
| A-NFR-05 | Pricing replayable | pure engine + stored payload | `test_pricing_is_deterministic`, replay test | Done |

Not started: A-FR-18 property-operations adapter (out of MVP scope by design).

## Decisions
1. **Quote endpoint** is `POST /v1/briefs/{id}/quotes` (create + price) rather than `POST /v1/quotes/{id}/price`;
   a quote never exists unpriced.
2. **Every quote needs a manager decision**, flagged or not (BO-A4: 100% of final quotes approved). Flags decide
   whether a written reason is required.
3. **Self-approval** blocks the quote creator *and* the brief requester. Route-level roles already stop managers
   from creating quotes; the service rule is tested on its own so it survives future role changes.
4. **Audit read access**: analysts and managers can read a brief's history (UX-A02 traceability); the raw
   aggregate audit endpoint stays auditor/admin only.
5. **Sessions** are stateless HMAC-signed tokens in an HttpOnly SameSite=Lax cookie (Bearer also accepted for API
   clients). `DH_DEMO_MODE=true` (default) allows password-less persona sign-in for the portfolio demo; production
   must set `DH_DEMO_MODE=false` and `DH_SESSION_SECRET`. No CSRF token yet: relies on SameSite=Lax + JSON bodies.
6. **Notifications** use a transactional outbox: written with the finalize transaction, delivered after commit,
   max 3 attempts, admin requeue. Receivers de-duplicate on the idempotency key.
7. **Finalized briefs are locked**: no re-extraction, edits or new quotes. A change means a new brief.
8. **Multi-region pricing** uses the most expensive requested region; **campaign months** = ceil(days/30).
9. **API port 8010**: port 8000 is used by another local project.

10. **Claude call shape**: `claude-opus-5-5`, adaptive thinking, `output_config.effort` (default `medium`,
    tune with the eval), JSON-schema structured output, server-side refusal fallback (`fallbacks: "default"`).
    `base_url` is pinned to `https://api.anthropic.com` so a shell-level `ANTHROPIC_BASE_URL` can't redirect calls.
11. **Provenance is enforced twice**: in the adapter and again in the service against the raw brief, for every
    provider. A value whose quote isn't in the brief is shown as "inferred — verify", never "found".
12. **PII** is replaced by placeholders before any provider call; the mapping stays in the process and only counts
    are stored. Quotes are restored locally so analysts see the original text.
13. **Eval scoring**: per-field tp / wrong / missed / invented / correctly-missing, plus pricing agreement through
    the real pricing engine, so business impact is measured, not just NLP accuracy.

14. **Deployment unit**: one container; FastAPI serves the built frontend and the API, so cookies are first-party
    and there is no CORS surface in production. Security headers (CSP, frame-ancestors none, nosniff,
    referrer policy) on every response; hashed assets cached for a year, API responses `no-store`.
15. **Web vitals** were measured locally (production build, desktop, no throttling): LCP 87 ms, CLS 0.00;
    Lighthouse desktop + mobile: Accessibility 100, Best Practices 100, SEO 100. Re-measure on the real host.

## Known limits
- Proposal export is HTML only (no PDF).
- Rate card publishing has no admin screen yet (API only).
- Docker image and CI workflow are written but not executed here (Docker is not installed on this machine and the
  repo has no GitHub remote yet).
- Mock extractor is a regex parser, clearly labelled. The Claude adapter is built and tested offline; no live
  Claude run has been made yet (needs an API key and approval of the ~$3 eval cost).
- The 48 generated eval cases share phrasing families, so a rule parser scores high on them (mock: 96.7% field
  match). The 12 hand-written hard cases are the discriminating part — read results per category.
- Name redaction only catches names after a contact cue ("Contact:", "PIC:", "cc"); free-floating names are not
  detected.
