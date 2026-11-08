"""Run the extraction evaluation and store a versioned report (A-FR-15, UAT-A13).

  uv run python -m app.eval.run                       # mock provider (free)
  uv run python -m app.eval.run --provider anthropic  # prints a cost estimate and stops
  uv run python -m app.eval.run --provider anthropic --yes [--limit 10] [--category pii]

Each run uses the same path production uses: PII redaction -> provider -> local restore ->
provenance check -> validation. Reports land in eval/reports/ with provider, model, prompt
version, effort and git commit, plus the delta against the previous comparable run.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.domain.requirement import REQUIRED_FIELDS, FieldValue
from app.eval.score import score_case, summarize
from app.llm.base import ExtractionError, get_provider
from app.llm.redact import redact
from app.services.briefs import _restore_and_verify as restore_and_verify

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "eval" / "cases.jsonl"
REPORTS = ROOT / "eval" / "reports"


def load_cases(limit: int | None, category: str | None) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]
    if category:
        cases = [c for c in cases if c["category"] == category]
    return cases[:limit] if limit else cases


def estimate_cost(cases: list[dict[str, Any]]) -> tuple[int, int, Decimal]:
    """Rough upper estimate for a paid run: ~3.5 chars/token input; ~2,000 output tokens per
    case (thinking + JSON at medium effort). Real figures are measured and reported."""
    from app.llm.claude import OUTPUT_SCHEMA, PRICES, SYSTEM_PROMPT

    s = get_settings()
    fixed = len(SYSTEM_PROMPT) + len(json.dumps(OUTPUT_SCHEMA))
    tin = sum(int((fixed + len(c["text"])) / 3.5) for c in cases)
    tout = 2000 * len(cases)
    pin, pout, _ = PRICES.get(s.llm_model, (Decimal(5), Decimal(25), Decimal(0)))
    return tin, tout, (tin * pin + tout * pout) / Decimal(1_000_000)


def git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def p95(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, round(0.95 * (len(s) - 1)))]


def run(provider_name: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    provider = get_provider(provider_name)
    results: list[dict[str, Any]] = []
    latencies: list[float] = []
    tokens_in = tokens_out = 0
    cost = Decimal(0)
    errors: dict[str, int] = {}
    pii_leaks: list[dict[str, Any]] = []
    model_version = prompt_version = None
    started = time.perf_counter()

    for case in cases:
        red = redact(case["text"])
        leaked = [p for p in case.get("pii", []) if p in red.text]
        if leaked:
            pii_leaks.append({"id": case["id"], "leaked": leaked})
        t0 = time.perf_counter()
        try:
            out = provider.extract(red.text, received_on=date.fromisoformat(case["received_on"]))
        except ExtractionError as e:
            latencies.append((time.perf_counter() - t0) * 1000)
            errors[e.error_class] = errors.get(e.error_class, 0) + 1
            if e.meta.get("cost_usd") is not None:
                cost += Decimal(e.meta["cost_usd"])
            results.append({"id": case["id"], "category": case["category"], "error": e.error_class})
            print(f"  {case['id']:10} ERROR {e.error_class}", file=sys.stderr)
            continue
        latencies.append((time.perf_counter() - t0) * 1000)
        model_version, prompt_version = out.model_version, out.prompt_version
        tokens_in += out.input_tokens or 0
        tokens_out += out.output_tokens or 0
        cost += out.cost_usd or Decimal(0)
        predicted = {
            f: restore_and_verify(out.fields.get(f, FieldValue()), red.mapping, case["text"])
            for f in REQUIRED_FIELDS
        }
        r = score_case(case, predicted)
        results.append(r)
        ok = sum(1 for b in r["buckets"].values() if b in ("tp", "tn"))
        print(f"  {case['id']:10} {ok}/7 fields", file=sys.stderr)

    s = get_settings()
    summary = summarize(results)
    summary.update({
        "pii_leaks": pii_leaks,
        "errors": errors,
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 1) if latencies else None,
            "median": round(statistics.median(latencies), 1) if latencies else None,
            "p95": round(p95(latencies), 1) if latencies else None,
        },
        "tokens": {"input": tokens_in, "output": tokens_out},
        "cost_usd": str(cost.quantize(Decimal("0.0001"))),
    })
    return {
        "meta": {
            "provider": provider_name,
            "model": model_version or getattr(provider, "model", None) or "n/a",
            "prompt_version": prompt_version,
            "effort": s.llm_effort if provider_name == "anthropic" else None,
            "git_commit": git_commit(),
            "cases_file": str(CASES.relative_to(ROOT)),
            "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "duration_s": round(time.perf_counter() - started, 2),
        },
        "summary": summary,
        "failures": [
            {"id": r["id"], "category": r["category"], **f}
            for r in results for f in r.get("failures", [])
        ],
        "results": results,
    }


def previous_report(meta: dict[str, Any]) -> dict[str, Any] | None:
    if not REPORTS.exists():
        return None
    for p in sorted(REPORTS.glob("*.json"), reverse=True):
        rep = json.loads(p.read_text())
        m = rep["meta"]
        if m["provider"] == meta["provider"] and m["model"] == meta["model"]:
            return rep
    return None


def markdown(rep: dict[str, Any], prev: dict[str, Any] | None) -> str:
    m, s = rep["meta"], rep["summary"]

    def pct(x: float | None) -> str:
        return "—" if x is None else f"{x * 100:.1f}%"

    def delta(key: str) -> str:
        if not prev or prev["summary"].get(key) is None or s.get(key) is None:
            return ""
        d = (s[key] - prev["summary"][key]) * 100
        return f" ({d:+.1f} pts vs {prev['meta']['started_at'][:10]})"

    lines = [
        f"# Extraction eval — {m['provider']} / {m['model']}",
        "",
        (f"Prompt `{m['prompt_version']}` · effort `{m['effort']}` · commit `{m['git_commit']}` · "
         f"{m['started_at']} · {s['cases']} cases ({s['errored_cases']} errored)"),
        "",
        "Simulated portfolio evaluation on synthetic briefs; not a claim about any company's data.",
        "",
        f"- **Field-level exact match:** {pct(s['field_exact_match'])}{delta('field_exact_match')} (target ≥ 90%)",
        f"- **Validation status agreement:** {pct(s['validation_agreement'])}{delta('validation_agreement')}",
        f"- **Pricing mismatch rate:** {pct(s['pricing_mismatch_rate'])} of valid briefs",
        f"- **PII leaks to provider:** {len(s['pii_leaks'])}",
        f"- **Values without a verified quote:** {s['unverified_values']}",
        f"- **Latency (ms):** mean {s['latency_ms']['mean']} · median {s['latency_ms']['median']} · p95 {s['latency_ms']['p95']}",
        f"- **Tokens:** {s['tokens']['input']:,} in / {s['tokens']['output']:,} out · **cost** ${s['cost_usd']}",
        f"- **Errors:** {s['errors'] or 'none'}",
        "",
        "| Field | Precision | Recall | Exact | tp / wrong / missed / invented / tn |",
        "|---|---|---|---|---|",
    ]
    for f, v in s["per_field"].items():
        c = v["counts"]
        lines.append(
            f"| {f} | {pct(v['precision'])} | {pct(v['recall'])} | {pct(v['exact'])} | "
            f"{c.get('tp', 0)} / {c.get('wrong', 0)} / {c.get('fn', 0)} / {c.get('halluc', 0)} / {c.get('tn', 0)} |"
        )
    lines += ["", "| Category | Exact |", "|---|---|"]
    lines += [f"| {k} | {pct(v)} |" for k, v in s["by_category"].items()]
    lines += ["", "## Error taxonomy (first 40)", ""]
    for f in rep["failures"][:40]:
        lines.append(
            f"- `{f['id']}` {f['field']} — **{f['outcome']}**: expected `{json.dumps(f['expected'], ensure_ascii=False)}`, "
            f"got `{json.dumps(f['got'], ensure_ascii=False)}`"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", default="mock", choices=["mock", "anthropic"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--category")
    ap.add_argument("--yes", action="store_true", help="confirm a paid run after seeing the estimate")
    args = ap.parse_args(argv)

    cases = load_cases(args.limit, args.category)
    if not cases:
        print("no cases selected", file=sys.stderr)
        return 1
    if args.provider != "mock":
        tin, tout, usd = estimate_cost(cases)
        print(f"{len(cases)} cases on {get_settings().llm_model} (effort {get_settings().llm_effort}): "
              f"~{tin:,} input + ~{tout:,} output tokens, roughly ${usd:.2f}.")
        if not args.yes:
            print("This calls a paid API. Re-run with --yes to proceed.")
            return 2

    report = run(args.provider, cases)
    prev = previous_report(report["meta"])
    if prev:
        report["meta"]["previous"] = prev["meta"]["started_at"]
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = REPORTS / f"{stamp}_{args.provider}_{report['meta']['model']}"
    base.with_suffix(".json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    md = markdown(report, prev)
    base.with_suffix(".md").write_text(md)
    print(md)
    print(f"saved {base.with_suffix('.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
