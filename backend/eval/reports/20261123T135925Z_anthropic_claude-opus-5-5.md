# Extraction eval — anthropic / claude-opus-5-5

Prompt `extract.claude.v2` · effort `medium` · commit `de61a68` · 2026-11-23T13:59:25+00:00 · 60 cases (0 errored)

Simulated portfolio evaluation on synthetic briefs; not a claim about any company's data.

- **Field-level exact match:** 99.8% (target ≥ 90%)
- **Validation status agreement:** 100.0%
- **Pricing mismatch rate:** 0.0% of valid briefs
- **PII leaks to provider:** 0
- **Values without a verified quote:** 0
- **Latency (ms):** mean 6576.8 · median 6558.9 · p95 8969.6
- **Tokens:** 187,107 in / 40,494 out · **cost** $1.5583
- **Errors:** none

| Field | Precision | Recall | Exact | tp / wrong / missed / invented / tn |
|---|---|---|---|---|
| client_sector | 100.0% | 100.0% | 100.0% | 60 / 0 / 0 / 0 / 0 |
| campaign_objective | 100.0% | 100.0% | 100.0% | 60 / 0 / 0 / 0 / 0 |
| audience | 98.2% | 100.0% | 98.3% | 55 / 0 / 0 / 1 / 4 |
| geo | 100.0% | 100.0% | 100.0% | 55 / 0 / 0 / 0 / 5 |
| deliverables | 100.0% | 100.0% | 100.0% | 56 / 0 / 0 / 0 / 4 |
| budget | 100.0% | 100.0% | 100.0% | 57 / 0 / 0 / 0 / 3 |
| timeline | 100.0% | 100.0% | 100.0% | 56 / 0 / 0 / 0 / 4 |

| Category | Exact |
|---|---|
| ambiguous | 100.0% |
| complete_en | 100.0% |
| complete_id | 100.0% |
| contradictory | 100.0% |
| currency | 100.0% |
| incomplete | 98.6% |
| mixed_language | 100.0% |
| multiple_amounts | 100.0% |
| nonstandard | 100.0% |
| out_of_catalog | 100.0% |
| pii | 100.0% |
| prompt_injection | 100.0% |

## Error taxonomy (first 40)

- `gen-028` audience — **halluc**: expected `null`, got `"open opportunities"`
