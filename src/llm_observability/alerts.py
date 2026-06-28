"""Alert rules + evaluation against trace windows.

Each rule is a (metric, op, threshold) triple scoped by feature/model.
Common rule shapes:

  - p95_latency_ms above 2000 for feature=email_classifier
  - error_rate above 0.05 (5%) for any feature
  - hourly_cost_usd above 1.00 for model=gpt-4o
  - calls_per_minute above 100 for any feature (rate spike)

The evaluator takes an aggregate dict (from aggregator.aggregate) and a
list of rules; returns the rules that fired with context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


# Metric names that rules can target.
Metric = Literal[
    "p95_latency_ms", "p99_latency_ms", "error_rate",
    "total_cost_usd", "feature_cost_usd", "model_cost_usd",
    "count", "feature_count",
]

Op = Literal[">", ">=", "<", "<=", "=="]


@dataclass
class AlertRule:
    """One alert rule."""
    name: str
    metric: Metric
    op: Op
    threshold: float
    # Scope filters - rule applies only when all match
    feature: str | None = None
    model: str | None = None
    tenant: str | None = None
    # Human-readable description for the alert payload
    description: str = ""


@dataclass
class Alert:
    """A fired alert with the context needed to action it."""
    rule_name: str
    metric: Metric
    threshold: float
    actual_value: float
    op: Op
    scope: dict[str, str]
    description: str


def check_alerts(aggregate: dict, rules: list[AlertRule]) -> list[Alert]:
    """Evaluate each rule against the aggregate. Return the fired alerts."""
    fired: list[Alert] = []
    for rule in rules:
        actual = _resolve_metric(aggregate, rule)
        if actual is None:
            continue
        if _check_threshold(actual, rule.op, rule.threshold):
            fired.append(Alert(
                rule_name=rule.name,
                metric=rule.metric,
                threshold=rule.threshold,
                actual_value=actual,
                op=rule.op,
                scope=_scope_dict(rule),
                description=rule.description or f"{rule.metric} {rule.op} {rule.threshold}",
            ))
    return fired


def _resolve_metric(aggregate: dict, rule: AlertRule) -> float | None:
    """Look up the metric value from the aggregate, scoped per rule."""
    if rule.metric == "total_cost_usd":
        return float(aggregate.get("total_cost_usd", 0))
    if rule.metric == "count":
        return float(aggregate.get("total_calls", 0))

    if rule.metric == "feature_cost_usd" and rule.feature:
        feat = aggregate.get("by_feature", {}).get(rule.feature)
        return float(feat["cost"]) if feat else None
    if rule.metric == "feature_count" and rule.feature:
        feat = aggregate.get("by_feature", {}).get(rule.feature)
        return float(feat["count"]) if feat else None
    if rule.metric == "model_cost_usd" and rule.model:
        m = aggregate.get("by_model", {}).get(rule.model)
        return float(m["cost"]) if m else None

    if rule.metric in ("p95_latency_ms", "p99_latency_ms", "error_rate"):
        if rule.feature:
            feat = aggregate.get("by_feature", {}).get(rule.feature)
            if not feat:
                return None
            if rule.metric == "p95_latency_ms":
                return float(feat.get("p95_latency_ms", 0))
            if rule.metric == "error_rate":
                count = feat.get("count", 0)
                if count == 0:
                    return 0.0
                return float(feat.get("error_count", 0)) / count
        # Global metrics
        if rule.metric == "error_rate":
            total = aggregate.get("total_calls", 0)
            if total == 0:
                return 0.0
            return float(aggregate.get("total_errors", 0)) / total
    return None


def _check_threshold(actual: float, op: Op, threshold: float) -> bool:
    if op == ">":
        return actual > threshold
    if op == ">=":
        return actual >= threshold
    if op == "<":
        return actual < threshold
    if op == "<=":
        return actual <= threshold
    if op == "==":
        return actual == threshold
    return False


def _scope_dict(rule: AlertRule) -> dict[str, str]:
    scope = {}
    if rule.feature:
        scope["feature"] = rule.feature
    if rule.model:
        scope["model"] = rule.model
    if rule.tenant:
        scope["tenant"] = rule.tenant
    return scope


def default_rules() -> list[AlertRule]:
    """A sensible starter set of rules. Customize per engagement."""
    return [
        AlertRule(
            name="hourly_total_cost_over_budget",
            metric="total_cost_usd", op=">", threshold=5.00,
            description="Total LLM spend in this window exceeded $5 - investigate volume or model choice.",
        ),
        AlertRule(
            name="error_rate_spike_global",
            metric="error_rate", op=">", threshold=0.05,
            description="Global error rate above 5% - check provider status or recent prompt changes.",
        ),
        AlertRule(
            name="classifier_latency_regression",
            metric="p95_latency_ms", op=">", threshold=2000,
            feature="customer_complaint_classifier",
            description="Classifier p95 latency exceeded 2s - investigate model or prompt size.",
        ),
        AlertRule(
            name="opus_cost_spike",
            metric="model_cost_usd", op=">", threshold=2.00,
            model="claude-opus-4-7",
            description="Opus model usage exceeded $2 in window - check whether Sonnet/Haiku would work.",
        ),
    ]
