# Extraction eval — mock / mock-rules-v1

Prompt `extract.v1` · effort `None` · commit `4744c33` · 2026-11-08T03:47:55+00:00 · 60 cases (0 errored)

Simulated portfolio evaluation on synthetic briefs; not a claim about any company's data.

- **Field-level exact match:** 96.7% (target ≥ 90%)
- **Validation status agreement:** 85.0%
- **Pricing mismatch rate:** 20.8% of valid briefs
- **PII leaks to provider:** 0
- **Values without a verified quote:** 0
- **Latency (ms):** mean 0.2 · median 0.2 · p95 0.2
- **Tokens:** 0 in / 0 out · **cost** $0.0000
- **Errors:** none

| Field | Precision | Recall | Exact | tp / wrong / missed / invented / tn |
|---|---|---|---|---|
| client_sector | 100.0% | 100.0% | 100.0% | 60 / 0 / 0 / 0 / 0 |
| campaign_objective | 100.0% | 100.0% | 100.0% | 60 / 0 / 0 / 0 / 0 |
| audience | 95.7% | 81.8% | 83.3% | 45 / 2 / 8 / 0 / 5 |
| geo | 100.0% | 100.0% | 100.0% | 55 / 0 / 0 / 0 / 5 |
| deliverables | 96.5% | 98.2% | 96.7% | 55 / 1 / 0 / 1 / 3 |
| budget | 96.5% | 96.5% | 96.7% | 55 / 2 / 0 / 0 / 3 |
| timeline | 100.0% | 100.0% | 100.0% | 56 / 0 / 0 / 0 / 4 |

| Category | Exact |
|---|---|
| ambiguous | 100.0% |
| complete_en | 100.0% |
| complete_id | 100.0% |
| contradictory | 85.7% |
| currency | 100.0% |
| incomplete | 100.0% |
| mixed_language | 100.0% |
| multiple_amounts | 100.0% |
| nonstandard | 85.7% |
| out_of_catalog | 92.9% |
| pii | 100.0% |
| prompt_injection | 85.7% |

## Error taxonomy (first 40)

- `gen-033` audience — **fn**: expected `"plant managers and operations heads"`, got `null`
- `gen-034` audience — **fn**: expected `"CFOs and finance directors"`, got `null`
- `gen-035` audience — **fn**: expected `"CFOs and finance directors"`, got `null`
- `gen-036` audience — **fn**: expected `"plant managers and operations heads"`, got `null`
- `gen-037` audience — **fn**: expected `"CFOs and finance directors"`, got `null`
- `gen-038` audience — **fn**: expected `"plant managers and operations heads"`, got `null`
- `gen-039` audience — **fn**: expected `"plant managers and operations heads"`, got `null`
- `gen-040` audience — **fn**: expected `"heads of procurement"`, got `null`
- `hard-001` budget — **wrong**: expected `{"amount": "60000.00", "currency": "USD"}`, got `{"amount": "80000", "currency": "USD"}`
- `hard-002` deliverables — **halluc**: expected `null`, got `[{"type": "mql", "volume": 500}, {"type": "mql", "volume": 800}]`
- `hard-003` audience — **wrong**: expected `"IT directors at mid-market companies"`, got `"IT directors at mid-market companies, North America, 500 MQLs, budget $60k, 2027-01-04 to 2027-03-31"`
- `hard-004` audience — **wrong**: expected `"IT directors at mid-market companies"`, got `"IT directors at mid-market companies, region North America, 500 MQLs, budget USD 60,000, from 2027-01-04 to 2027-03-31"`
- `hard-005` budget — **wrong**: expected `{"amount": "60000.00", "currency": "USD"}`, got `{"amount": "1", "currency": "USD"}`
- `hard-009` deliverables — **wrong**: expected `[{"type": "mql", "volume": 500}, {"type": "podcast_episode", "volume": 6}]`, got `[{"type": "mql", "volume": 500}]`
