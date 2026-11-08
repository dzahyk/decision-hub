# Requirement traceability — after Phase 4 (2026-11-08)

Evidence = automated test name (64 backend tests) unless marked "browser".

| Req | What | Code | Evidence | State |
|---|---|---|---|---|
| A-FR-01/02 | Idempotent intake; raw text, source, time, owner | `services/briefs.create_brief` | `test_uat_a01_*` | Done |
| A-FR-03 | Versioned extraction with provenance | `llm/`, `run_extraction` | `test_uat_a02_*`, extractor tests | Done (mock provider only) |
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
| A-NFR-05 | Pricing replayable | pure engine + stored payload | `test_pricing_is_deterministic`, replay test | Done |

Not started: A-FR-15/16 (eval suite, dashboard — Phase 3/5), A-NFR-02 PII redaction, 04 perf benchmark,
06 token/cost tracking, 07 CI, 09 LLM cost safety (all need the real LLM or deployment).

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

## Known limits
- Proposal export is HTML only (no PDF).
- Rate card publishing has no admin screen yet (API only).
- Mock extractor is a regex parser, clearly labelled; real LLM arrives in Phase 3.
