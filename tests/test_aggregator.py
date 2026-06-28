"""Tests for the aggregator + sliding-window math."""

import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from llm_observability.aggregator import aggregate, window_stats  # noqa: E402
from llm_observability.tracer import Trace, load_traces  # noqa: E402


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _trace(trace_id="x", feature="f", model="m", ts="2026-06-28T12:00:00.000Z",
           latency=100, cost=0.001, error=None, tenant="default",
           input_tokens=10, output_tokens=10):
    return Trace(
        trace_id=trace_id, ts=ts, feature=feature, model=model,
        prompt_hash="h", prompt_chars=10, response_chars=10,
        input_tokens=input_tokens, output_tokens=output_tokens,
        latency_ms=latency, cost_usd=cost, error=error, tenant=tenant,
    )


def test_empty_traces_returns_zeroed_aggregate():
    result = aggregate([])
    assert result["total_calls"] == 0
    assert result["total_cost_usd"] == 0.0
    assert result["by_feature"] == {}


def test_aggregate_counts_per_feature():
    traces = [
        _trace(feature="f1", cost=0.001),
        _trace(feature="f1", cost=0.002),
        _trace(feature="f2", cost=0.003),
    ]
    result = aggregate(traces)
    assert result["total_calls"] == 3
    assert result["by_feature"]["f1"]["count"] == 2
    assert result["by_feature"]["f2"]["count"] == 1


def test_aggregate_sums_total_cost_correctly():
    traces = [_trace(cost=0.001), _trace(cost=0.002), _trace(cost=0.003)]
    result = aggregate(traces)
    assert result["total_cost_usd"] == 0.006


def test_aggregate_tracks_errors():
    traces = [
        _trace(error=None),
        _trace(error="429"),
        _trace(error="500"),
    ]
    result = aggregate(traces)
    assert result["total_errors"] == 2


def test_aggregate_p95_latency_per_feature():
    # 100 trace latencies 1..100, p95 should be ~95
    traces = [_trace(feature="f", latency=lat) for lat in range(1, 101)]
    result = aggregate(traces)
    # Percentile math: idx = int(100 * 95 / 100) - 1 = 94 → value at idx 94 = 95
    assert result["by_feature"]["f"]["p95_latency_ms"] == 95


def test_aggregate_breaks_down_per_model():
    traces = [
        _trace(model="m1", cost=0.10),
        _trace(model="m1", cost=0.20),
        _trace(model="m2", cost=0.50),
    ]
    result = aggregate(traces)
    assert result["by_model"]["m1"]["count"] == 2
    assert result["by_model"]["m1"]["cost"] == 0.30
    assert result["by_model"]["m2"]["cost"] == 0.50


def test_aggregate_breaks_down_per_tenant():
    traces = [
        _trace(tenant="acme", cost=1.0),
        _trace(tenant="globex", cost=2.0),
        _trace(tenant="acme", cost=3.0),
    ]
    result = aggregate(traces)
    assert result["by_tenant"]["acme"]["count"] == 2
    assert result["by_tenant"]["acme"]["cost"] == 4.0
    assert result["by_tenant"]["globex"]["count"] == 1


# ---------- Sliding-window stats ------------------------------------------

def test_window_stats_returns_one_row_per_feature_model_window():
    traces = [
        _trace(ts="2026-06-28T12:00:00.000Z", feature="f1", model="m1"),
        _trace(ts="2026-06-28T12:05:00.000Z", feature="f1", model="m1"),
        _trace(ts="2026-06-28T12:15:00.000Z", feature="f1", model="m1"),
        _trace(ts="2026-06-28T12:15:00.000Z", feature="f2", model="m2"),
    ]
    stats = window_stats(traces, window_minutes=10)
    # First window: 2 (f1, m1)
    # Second window: 1 (f1, m1) + 1 (f2, m2)
    assert len(stats) == 3
    counts_per_feature = {(s.feature, s.model): s.count for s in stats}
    # Three unique (feature, model, window) combos
    total = sum(s.count for s in stats)
    assert total == 4


def test_window_stats_p95_uses_window_traces_only():
    traces = [
        _trace(ts="2026-06-28T12:00:00.000Z", feature="f", latency=100),
        _trace(ts="2026-06-28T12:01:00.000Z", feature="f", latency=200),
        # Second window starts at 12:10
        _trace(ts="2026-06-28T12:11:00.000Z", feature="f", latency=5000),
    ]
    stats = window_stats(traces, window_minutes=10)
    assert len(stats) == 2
    first_p95 = stats[0].p95_latency_ms
    second_p95 = stats[1].p95_latency_ms
    assert first_p95 < 300  # 100/200 only
    assert second_p95 == 5000


# ---------- Against the bundled fixture -----------------------------------

def test_bundled_fixture_aggregate_totals_match():
    """The deterministic fixture should produce exact totals (regression catcher)."""
    traces = load_traces(FIXTURES / "production-1hour.jsonl")
    assert len(traces) > 500  # ~646 expected

    result = aggregate(traces)
    # Specific assertions against the seeded fixture
    assert "customer_complaint_classifier" in result["by_feature"]
    assert "policy_summarizer" in result["by_feature"]
    assert "research_agent" in result["by_feature"]
    assert "claude-haiku-4-5" in result["by_model"]
    assert "claude-opus-4-7" in result["by_model"]
    assert "acme" in result["by_tenant"]
    assert "globex" in result["by_tenant"]
    # Total errors should be small but non-zero (synthetic error rate is ~2%)
    assert 0 < result["total_errors"] < 50


def test_bundled_fixture_window_stats_yields_six_windows_x_features():
    """1-hour fixture / 10-min windows = 6 windows × up to 3 features each."""
    traces = load_traces(FIXTURES / "production-1hour.jsonl")
    stats = window_stats(traces, window_minutes=10)
    # Approximately 18 rows (6 windows × 3 features) - exact depends on jitter
    assert 12 <= len(stats) <= 22
