# Requirement traceability — Slice 0 (2026-11-08)

| Req | What | Code | Evidence | State |
|---|---|---|---|---|
| A-FR-01 | Brief intake, unique request, idempotent | `services/briefs.create_brief`, `POST /v1/briefs` | `test_uat_a01_*` (2) | Done |
| A-FR-02 | Raw text, source, time, owner stored | `models.Brief` | `test_uat_a01_duplicate…` | Done |
| A-FR-03 | Extraction to versioned schema + provenance | `llm/`, `run_extraction` | `test_uat_a02_*`, extractor tests | Done (mock provider only) |
| A-FR-04 | Validator rejects missing / wrong type / bad enum | `domain/requirement.py` | `test_malformed_values…` (8 cases), `test_uat_a03_*` | Done |
| A-FR-05 | Analyst correction, raw untouched | `edit_requirements` | `test_uat_a04_*` | Done |
| A-FR-06 | Deterministic price / cost / margin | `domain/pricing.py` | `test_pricing.py` oracle + 50× replay | Done |
| A-FR-07 | Floor margin, discount, capacity flags | `domain/pricing.py` | `test_uat_a06_*`, pricing tests | Done |
| A-FR-10 | Audit events: actor, action, before/after, time | `_audit` in same transaction | `test_uat_a09_*` | Partial (no approval events yet) |
| A-FR-13 | UI: status, incomplete reasons, history | `frontend/src/Detail.tsx` | Browser check 2026-11-08 | Partial |
| A-BR-01 | No quote without all required fields | `create_quote` guard | `test_uat_a03_*` | Done |
| A-BR-02 | Quote stores rate card version | `Quote.rate_card_version` | `test_uat_a05_*` | Done |
| A-BR-03 | >10% discount / low margin → manager | `Flag.requires="manager"` | `test_uat_a06_*`, boundary 10 vs 10.01 | Flag done; approval Phase 4 |
| A-BR-04 | LLM can't touch rates/approvals | Adapter returns only schema fields | `test_instructions_inside_the_brief…` | Done for mock; re-test with real LLM |
| A-BR-08 | Off-catalog → feasibility review | `out_of_catalog` flag | `test_out_of_catalog…` | Flag done; ops note Phase 4 |
| A-NFR-05 | Pricing replayable | Pure function + stored `pricing` JSON | `test_pricing_is_deterministic` | Done |
| A-NFR-08 | Status not by colour alone, keyboard | Chips carry glyph + text; focus rings | Manual | Partial (no axe audit yet) |

Not started: A-FR-08/09/11/12/14/15/16/17, A-NFR-01 (real auth), 02 (PII redaction), 03, 04, 06 (token/cost), 07 (CI), 09.

## Decisions
1. **Quote endpoint** is `POST /v1/briefs/{id}/quotes` (create + price in one step) instead of the
   spec's `POST /v1/quotes/{id}/price`, because a quote never exists unpriced. Revisit in Phase 4.
2. **Audit read access** includes analyst and manager (spec: auditor/admin) so reviewers can trace
   field → rule → decision (UX-A02).
3. **Multi-region pricing** uses the most expensive requested region's multipliers. Conservative
   and easy to explain; a per-region volume split needs input the brief rarely has.
4. **Campaign months** = ceil(days / 30), minimum 1. A Dec 1 – Jan 31 campaign (61 days) counts as
   3 months of capacity. Simple and documented; may be tightened to calendar months.
5. **API port 8010**: port 8000 is used by another local project on this machine.
6. **Mock extractor** is a regex parser, labelled "mock rules (not an LLM)" in API and UI, so no
   output is presented as AI. It ignores instructions in the brief by construction.
