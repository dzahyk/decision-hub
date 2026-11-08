"""Pricing engine benchmark (A-NFR-04: p95 rule calculation < 1 s on 1,000 local fixtures).

  uv run python scripts/bench_pricing.py [--n 1000]

Prints environment, p50/p95/max per-quote latency and total time. Deterministic fixtures
(seeded), so runs are comparable across machines.
"""

from __future__ import annotations

import argparse
import platform
import random
import statistics
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.pricing import RATE_CARD_V1, price_requirement

TYPES = [*RATE_CARD_V1["catalog"], "video_production"]
REGIONS = list(RATE_CARD_V1["geo_multipliers"])


def fixtures(n: int, seed: int = 4) -> list[tuple[dict, str]]:
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        k = rng.randint(1, 4)
        req = {
            "client_sector": "technology", "campaign_objective": "lead_generation", "audience": "x",
            "geo": rng.sample(REGIONS, rng.randint(1, 3)),
            "deliverables": [{"type": rng.choice(TYPES), "volume": rng.randint(1, 5000)} for _ in range(k)],
            "budget": {"amount": f"{rng.randint(5, 500) * 1000}.00", "currency": "USD"},
            "timeline": {"start_date": None, "end_date": f"2027-{rng.randint(1, 12):02d}-28"},
        }
        out.append((req, f"{rng.randint(0, 3000) / 100:.2f}"))
    return out


def run(n: int = 1000) -> dict[str, float]:
    ref = date(2026, 11, 8)
    times = []
    data = fixtures(n)
    t0 = time.perf_counter()
    for req, disc in data:
        s = time.perf_counter()
        price_requirement(req, RATE_CARD_V1, discount_pct=disc, reference_date=ref)
        times.append(time.perf_counter() - s)
    total = time.perf_counter() - t0
    times.sort()
    return {
        "n": n,
        "p50_ms": statistics.median(times) * 1000,
        "p95_ms": times[min(n - 1, round(0.95 * (n - 1)))] * 1000,
        "max_ms": times[-1] * 1000,
        "total_s": total,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    r = run(ap.parse_args().n)
    print(f"python {platform.python_version()} · {platform.machine()} · {platform.system()} {platform.release()}")
    print(f"{r['n']} quotes · p50 {r['p50_ms']:.3f} ms · p95 {r['p95_ms']:.3f} ms · "
          f"max {r['max_ms']:.3f} ms · total {r['total_s']:.3f} s  (target: p95 < 1000 ms)")
