"""Tests for the alert rules engine."""

import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from llm_observability.aggregator import aggregate  # noqa: E402
from llm_observability.alerts import (  # noqa: E402
    AlertRule, check_alerts, default_rules,
)
from llm_observability.tracer import Trace, load_traces  # noqa: E402


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _trace(**kwargs):
    defaults = dict(trace_id="x", ts="2026-06-28T12:00:00.000Z",
                    feature="f", model="m", prompt_hash="h",
                    prompt_chars=10, response_chars=10,
                    input_tokens=10, output_tokens=10,
                    latency_ms=100, cost_usd=0.001)
    defaults.update(kwargs)
    return Trace(**defaults)


def test_default_rules_returns_starter_set():
    rules = default_rules()
    assert len(rules) >= 3
    names = {r.name for r in rules}
    assert "hourly_total_cost_over_budget" in names


def test_rule_fires_on_total_cost_above_threshold():
    traces = [_trace(cost_usd=10.0)]
    agg = aggregate(traces)
    rule = AlertRule(
        name="cost_test", metric="total_cost_usd", op=">", threshold=5.0,
    )
    fired = check_alerts(agg, [rule])
    assert len(fired) == 1
    assert fired[0].actual_value == 10.0


def test_rule_does_not_fire_when_below_threshold():
    traces = [_trace(cost_usd=1.0)]
    agg = aggregate(traces)
    rule = AlertRule(
        name="cost_test", metric="total_cost_usd", op=">", threshold=5.0,
    )
    fired = check_alerts(agg, [rule])
    assert fired == []


def test_feature_scoped_rule_only_fires_for_scoped_feature():
    traces = [
        _trace(feature="f1", cost_usd=10.0),
        _trace(feature="f2", cost_usd=10.0),
    ]
    agg = aggregate(traces)
    rule = AlertRule(
        name="f1_only", metric="feature_cost_usd", op=">", threshold=5.0,
        feature="f1",
    )
    fired = check_alerts(agg, [rule])
    assert len(fired) == 1
    assert fired[0].scope.get("feature") == "f1"


def test_model_scoped_rule_only_evaluates_that_model():
    traces = [
        _trace(model="m1", cost_usd=5.0),
        _trace(model="m2", cost_usd=10.0),
    ]
    agg = aggregate(traces)
    rule_m1 = AlertRule(
        name="m1", metric="model_cost_usd", op=">", threshold=4.0, model="m1",
    )
    rule_m2 = AlertRule(
        name="m2", metric="model_cost_usd", op=">", threshold=4.0, model="m2",
    )
    fired = check_alerts(agg, [rule_m1, rule_m2])
    assert {f.rule_name for f in fired} == {"m1", "m2"}


def test_error_rate_rule_uses_per_feature_error_rate():
    # 10 calls in f1, 5 errors → 50% error rate
    traces = (
        [_trace(feature="f1", error="x") for _ in range(5)] +
        [_trace(feature="f1") for _ in range(5)]
    )
    agg = aggregate(traces)
    rule = AlertRule(
        name="spike", metric="error_rate", op=">", threshold=0.10,
        feature="f1",
    )
    fired = check_alerts(agg, [rule])
    assert len(fired) == 1
    assert fired[0].actual_value == 0.5


def test_error_rate_rule_global_when_no_feature_scope():
    traces = (
        [_trace(error="x") for _ in range(3)] +
        [_trace() for _ in range(7)]
    )
    agg = aggregate(traces)
    rule = AlertRule(
        name="global_spike", metric="error_rate", op=">", threshold=0.20,
    )
    fired = check_alerts(agg, [rule])
    assert len(fired) == 1
    assert fired[0].actual_value == 0.3


def test_p95_latency_rule_uses_feature_p95():
    # 90 fast + 10 slow -> p95 sits in the slow tail
    traces = [
        _trace(feature="slow_one", latency_ms=lat) for lat in [100] * 90 + [3000] * 10
    ]
    agg = aggregate(traces)
    rule = AlertRule(
        name="slow", metric="p95_latency_ms", op=">", threshold=2000,
        feature="slow_one",
    )
    fired = check_alerts(agg, [rule])
    assert len(fired) == 1


def test_rule_with_missing_scope_returns_none_metric():
    """A rule scoped to a feature that doesn't exist shouldn't fire."""
    traces = [_trace(feature="f1")]
    agg = aggregate(traces)
    rule = AlertRule(
        name="ghost", metric="feature_cost_usd", op=">", threshold=0.0,
        feature="nonexistent",
    )
    fired = check_alerts(agg, [rule])
    assert fired == []


# ---------- Default rules against the bundled fixture --------------------

def test_default_rules_fire_opus_spike_on_fixture():
    """The bundled fixture has the Opus 'research_agent' burning ~$3.50 -
    the default opus_cost_spike rule (threshold $2) should fire."""
    traces = load_traces(FIXTURES / "production-1hour.jsonl")
    agg = aggregate(traces)
    fired = check_alerts(agg, default_rules())
    names = {f.rule_name for f in fired}
    assert "opus_cost_spike" in names
