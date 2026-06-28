"""Sliding window aggregations over Trace records.

Compute the things you actually want on your dashboard:
  - p50 / p95 / p99 latency
  - error rate
  - total cost in window
  - calls per minute
  - per-feature / per-model / per-tenant breakdowns

Pure functions over a list of Traces - no time-series DB required for the
demo. For production scale (millions of traces), swap the aggregation
backend to ClickHouse / DuckDB / Datadog metrics.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .tracer import Trace


@dataclass
class WindowStats:
    """Aggregate stats for one (feature, model) pair in one time window."""
    feature: str
    model: str
    window_start: str
    window_end: str
    count: int
    error_count: int
    error_rate: float
    p50_latency_ms: int
    p95_latency_ms: int
    p99_latency_ms: int
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int


def aggregate(traces: list[Trace]) -> dict:
    """Single-window aggregation across all traces, broken down by feature + model.

    Useful for "snapshot of the last N minutes" style dashboards.

    Returns: {
        "total_calls": int,
        "total_cost_usd": float,
        "total_errors": int,
        "by_feature": {feature: {count, error_count, cost, p95_latency_ms}},
        "by_model": {model: {count, cost}},
        "by_tenant": {tenant: {count, cost}},
    }
    """
    if not traces:
        return {"total_calls": 0, "total_cost_usd": 0.0, "total_errors": 0,
                "by_feature": {}, "by_model": {}, "by_tenant": {}}

    by_feature: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "error_count": 0, "cost": 0.0, "latencies": []}
    )
    by_model: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "cost": 0.0})
    by_tenant: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "cost": 0.0})

    total_cost = 0.0
    total_errors = 0
    for t in traces:
        by_feature[t.feature]["count"] += 1
        if t.error:
            by_feature[t.feature]["error_count"] += 1
            total_errors += 1
        by_feature[t.feature]["cost"] += t.cost_usd
        by_feature[t.feature]["latencies"].append(t.latency_ms)
        by_model[t.model]["count"] += 1
        by_model[t.model]["cost"] += t.cost_usd
        by_tenant[t.tenant]["count"] += 1
        by_tenant[t.tenant]["cost"] += t.cost_usd
        total_cost += t.cost_usd

    # Finalize per-feature p95
    for feature, data in by_feature.items():
        latencies = sorted(data["latencies"])
        data["p95_latency_ms"] = _percentile(latencies, 95)
        data["cost"] = round(data["cost"], 4)
        del data["latencies"]  # don't ship the raw list in the summary

    # Round costs in the model + tenant breakdowns too
    for m in by_model.values():
        m["cost"] = round(m["cost"], 4)
    for t_data in by_tenant.values():
        t_data["cost"] = round(t_data["cost"], 4)

    return {
        "total_calls": len(traces),
        "total_cost_usd": round(total_cost, 4),
        "total_errors": total_errors,
        "by_feature": dict(by_feature),
        "by_model": dict(by_model),
        "by_tenant": dict(by_tenant),
    }


def window_stats(traces: list[Trace], window_minutes: int = 60) -> list[WindowStats]:
    """Break traces into time windows; produce WindowStats per (feature, model) per window.

    Useful for time-series charts. Returns one row per (feature, model, window).
    """
    if not traces:
        return []

    # Sort by timestamp
    sorted_traces = sorted(traces, key=lambda t: t.ts)
    first_ts = _parse_ts(sorted_traces[0].ts)
    last_ts = _parse_ts(sorted_traces[-1].ts)

    # Build windows
    window_size = timedelta(minutes=window_minutes)
    windows: list[tuple[datetime, datetime]] = []
    cur = first_ts
    while cur <= last_ts:
        windows.append((cur, cur + window_size))
        cur += window_size

    # Bucket traces into (window_index, feature, model)
    buckets: dict[tuple[int, str, str], list[Trace]] = defaultdict(list)
    for t in sorted_traces:
        ts = _parse_ts(t.ts)
        for i, (start, end) in enumerate(windows):
            if start <= ts < end:
                buckets[(i, t.feature, t.model)].append(t)
                break

    # Compute stats per bucket
    results: list[WindowStats] = []
    for (window_i, feature, model), bucket_traces in sorted(buckets.items()):
        latencies = sorted(t.latency_ms for t in bucket_traces)
        error_count = sum(1 for t in bucket_traces if t.error)
        total_cost = sum(t.cost_usd for t in bucket_traces)
        total_input = sum(t.input_tokens for t in bucket_traces)
        total_output = sum(t.output_tokens for t in bucket_traces)
        start, end = windows[window_i]
        results.append(WindowStats(
            feature=feature, model=model,
            window_start=start.isoformat().replace("+00:00", "Z"),
            window_end=end.isoformat().replace("+00:00", "Z"),
            count=len(bucket_traces),
            error_count=error_count,
            error_rate=round(error_count / len(bucket_traces), 4) if bucket_traces else 0.0,
            p50_latency_ms=_percentile(latencies, 50),
            p95_latency_ms=_percentile(latencies, 95),
            p99_latency_ms=_percentile(latencies, 99),
            total_cost_usd=round(total_cost, 4),
            total_input_tokens=total_input,
            total_output_tokens=total_output,
        ))
    return results


# ----- Helpers --------------------------------------------------------------

def _percentile(sorted_values: list[int], pct: int) -> int:
    if not sorted_values:
        return 0
    idx = max(0, int(len(sorted_values) * pct / 100) - 1)
    return int(sorted_values[idx])


def _parse_ts(iso_str: str) -> datetime:
    """Parse the trace's ISO-8601 string back to a datetime (UTC)."""
    s = iso_str.rstrip("Z")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return datetime.now(timezone.utc)
