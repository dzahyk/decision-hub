# Extraction eval — anthropic / claude-opus-5-5

> **Failed run, kept on purpose.** All 60 requests were rejected (HTTP 400, `bad_request`) before the model
> answered, so no tokens were used and no scores exist. Cause: the output schema had 22 nullable fields and the API
> accepts at most 16. Fixed in commit `1ebfd6a`. `Prompt None` and the empty tables below come from this failure.
> The successful run is [20261123T135925Z](20261123T135925Z_anthropic_claude-opus-5-5.md).

Prompt `None` · effort `medium` · commit `de61a68` · 2026-11-23T13:50:52+00:00 · 60 cases (60 errored)

Simulated portfolio evaluation on synthetic briefs; not a claim about any company's data.

- **Field-level exact match:** — (target ≥ 90%)
- **Validation status agreement:** —
- **Pricing mismatch rate:** — of valid briefs
- **PII leaks to provider:** 0
- **Values without a verified quote:** 0
- **Latency (ms):** mean 378.3 · median 351.0 · p95 453.3
- **Tokens:** 0 in / 0 out · **cost** $0.0000
- **Errors:** {'bad_request': 60}

| Field | Precision | Recall | Exact | tp / wrong / missed / invented / tn |
|---|---|---|---|---|
| client_sector | — | — | — | 0 / 0 / 0 / 0 / 0 |
| campaign_objective | — | — | — | 0 / 0 / 0 / 0 / 0 |
| audience | — | — | — | 0 / 0 / 0 / 0 / 0 |
| geo | — | — | — | 0 / 0 / 0 / 0 / 0 |
| deliverables | — | — | — | 0 / 0 / 0 / 0 / 0 |
| budget | — | — | — | 0 / 0 / 0 / 0 / 0 |
| timeline | — | — | — | 0 / 0 / 0 / 0 / 0 |

| Category | Exact |
|---|---|

## Error taxonomy (first 40)

