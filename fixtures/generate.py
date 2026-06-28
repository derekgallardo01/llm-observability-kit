"""Generate a deterministic 1-hour trace fixture for the demo + evals.

Produces traces representing realistic production traffic:
  - 3 features: classifier (high volume, low latency, low cost),
                summarizer (medium volume, medium latency, medium cost),
                research_agent (low volume, high latency, high cost - Opus)
  - 2 tenants (acme, globex) with different volume splits
  - Occasional errors (~2% rate)
  - Realistic latency distributions per model
  - Realistic cost distribution

Deterministic via a fixed RNG seed so the eval suite asserts exact numbers.
"""

from __future__ import annotations

import json
import random
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from llm_observability.tracer import Trace, DEFAULT_PRICING  # noqa: E402


# Fixed seed - the eval suite asserts exact aggregated values.
SEED = 42

# Window start - fixed so timestamps are deterministic.
WINDOW_START = datetime(2026, 6, 28, 9, 0, 0, tzinfo=timezone.utc)


def make_feature_config():
    return {
        "customer_complaint_classifier": {
            "model": "claude-haiku-4-5",
            "calls_per_minute": 8,
            "p50_latency_ms": 350,
            "latency_jitter_ms": 120,
            "avg_input_tokens": 200,
            "avg_output_tokens": 8,
            "error_rate": 0.005,
            "tenants": ["acme", "globex"],
            "tenant_weights": [0.7, 0.3],
        },
        "policy_summarizer": {
            "model": "claude-sonnet-4-6",
            "calls_per_minute": 2,
            "p50_latency_ms": 1100,
            "latency_jitter_ms": 400,
            "avg_input_tokens": 1200,
            "avg_output_tokens": 220,
            "error_rate": 0.01,
            "tenants": ["acme", "globex"],
            "tenant_weights": [0.5, 0.5],
        },
        "research_agent": {
            "model": "claude-opus-4-7",
            "calls_per_minute": 0.5,  # 1 every 2 mins
            "p50_latency_ms": 3500,
            "latency_jitter_ms": 1500,
            "avg_input_tokens": 4500,
            "avg_output_tokens": 800,
            "error_rate": 0.05,
            "tenants": ["acme"],
            "tenant_weights": [1.0],
        },
    }


def generate_hour(seed: int = SEED) -> list[Trace]:
    rng = random.Random(seed)
    features = make_feature_config()
    traces: list[Trace] = []
    minutes = 60
    counter = 0

    for minute in range(minutes):
        for feature_name, cfg in features.items():
            calls = rng.poisson(cfg["calls_per_minute"]) if hasattr(rng, "poisson") else _poisson(rng, cfg["calls_per_minute"])
            for _ in range(calls):
                counter += 1
                ts = WINDOW_START + timedelta(minutes=minute, seconds=rng.uniform(0, 60))
                input_tokens = int(rng.gauss(cfg["avg_input_tokens"], cfg["avg_input_tokens"] * 0.15))
                input_tokens = max(1, input_tokens)
                output_tokens = int(rng.gauss(cfg["avg_output_tokens"], cfg["avg_output_tokens"] * 0.15))
                output_tokens = max(1, output_tokens)
                latency = int(rng.gauss(cfg["p50_latency_ms"], cfg["latency_jitter_ms"]))
                latency = max(50, latency)
                error = "RateLimitError: 429" if rng.random() < cfg["error_rate"] else None
                tenant = rng.choices(cfg["tenants"], weights=cfg["tenant_weights"])[0]

                pricing = DEFAULT_PRICING.get(cfg["model"], {"input": 0, "output": 0})
                cost = round((input_tokens / 1000) * pricing["input"] +
                              (output_tokens / 1000) * pricing["output"], 6)

                traces.append(Trace(
                    trace_id=f"trace-{counter:05d}",
                    ts=ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z",
                    feature=feature_name,
                    model=cfg["model"],
                    prompt_hash=f"hash-{counter % 100:03d}",  # repeats are realistic
                    prompt_chars=input_tokens * 4,
                    response_chars=output_tokens * 4 if not error else 0,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens if not error else 0,
                    latency_ms=latency,
                    cost_usd=cost if not error else 0.0,
                    error=error,
                    tenant=tenant,
                ))
    return traces


def _poisson(rng: random.Random, lam: float) -> int:
    """Cheap Poisson sampler for the call-rate jitter."""
    import math
    L = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= L:
            break
    return k - 1


def main() -> int:
    traces = generate_hour()
    out_path = Path(__file__).resolve().parent / "production-1hour.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for t in traces:
            f.write(json.dumps(asdict(t)) + "\n")
    print(f"Wrote {len(traces)} traces to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
