# Evaluation

The eval suite asserts **specific aggregation properties** against
the bundled deterministic fixture. Catches regressions in:

- Aggregate math (per-feature/model/tenant counts + cost sums)
- Percentile calculations
- Alert rule evaluation

## What gets checked

Per `evals/golden.json`, each case is `(fixture, type, assertions or rule expectations)`:

```json
{
  "id": "research-agent-is-the-cost-leader",
  "fixture": "production-1hour.jsonl",
  "type": "aggregate",
  "assertions": [
    {"path": "by_feature.research_agent.cost", "op": ">=", "value": 3.0},
    {"path": "by_feature.customer_complaint_classifier.cost", "op": "<=", "value": 0.05}
  ]
}
```

Two case types:
- **`aggregate`** — assertions on path/op/value against the aggregate dict
- **`alert`** — assertion that a named rule fires (or doesn't) against the aggregate

Supported assertion ops: `==`, `>=`, `<=`, `>`, `<`.

## Running

```bash
python evals/run.py
```

Output:

```
Running 5 eval cases against backend=stub

  PASS  fixture-contains-all-three-features
        OK   by_feature.customer_complaint_classifier.count=503 (expected >= 400)
        OK   by_feature.policy_summarizer.count=115 (expected >= 80)
        OK   by_feature.research_agent.count=28 (expected >= 10)
  PASS  research-agent-is-the-cost-leader
        OK   by_feature.research_agent.cost=3.636 (expected >= 3.0)
        OK   by_feature.customer_complaint_classifier.cost=0.0297 (expected <= 0.05)
  PASS  opus-cost-spike-alert-fires-on-fixture
        OK   rule 'opus_cost_spike': fired (expected fired)
  PASS  error-rate-rule-does-not-fire-on-fixture
        OK   rule 'error_rate_spike_global': did not fire (expected did not fire)
  PASS  tenant-split-acme-larger-than-globex
        OK   by_tenant.acme.count=437 (expected > 300)
        OK   by_tenant.globex.count=209 (expected > 50)

  5/5 cases passed (100%)
```

CI gates on 100%.

## Why a deterministic fixture

The fixture (`fixtures/production-1hour.jsonl`) is generated from a
**fixed RNG seed** (`SEED = 42` in `fixtures/generate.py`). This
means:

- Trace count is exact (646)
- Per-feature counts are exact (503 / 115 / 28)
- Per-model costs are exact ($3.64 Opus, $0.79 Sonnet, $0.03 Haiku)
- Error count is exact (5)

Without determinism, the eval suite would be `>=` everywhere with
soft thresholds, and a real regression could slip through as
"within noise."

To regenerate the fixture after changing the generator:

```bash
python fixtures/generate.py
```

Then update any eval cases that asserted exact numbers if the
generator changes the seed or the workload shape.

## Adding cases

Edit `evals/golden.json`:

```json
{
  "id": "your-new-case",
  "fixture": "production-1hour.jsonl",
  "type": "aggregate",
  "assertions": [
    {"path": "by_feature.YOUR_FEATURE.p95_latency_ms", "op": "<=", "value": 1000}
  ]
}
```

Re-run. If the assertion fails on the deterministic fixture, either
your expectation is wrong or the aggregator regressed.

## Adding cases against your real traces

Production observability cases are different — they assert
**properties of your live workload**:

```json
{
  "id": "production-classifier-p95-budget",
  "fixture": "/var/log/llm-traces.jsonl",
  "type": "aggregate",
  "assertions": [
    {"path": "by_feature.customer_complaint_classifier.p95_latency_ms",
     "op": "<=", "value": 1500}
  ]
}
```

Run as a scheduled job. The 1500ms threshold is your SLA. If
production drifts above it, the eval fails and your alerting fires.

This pattern lets you use the same harness for synthetic regressions
(in CI) and production SLO monitoring (in cron).

## The aggregator as the regression net

When you refactor `aggregator.py` or `tracer.py`, the eval suite is
what tells you whether your refactor changed the output numbers. Any
change to:

- The `_percentile` formula
- The cost rounding
- The error counting
- The per-tenant breakdowns

will flip one or more eval cases. Treat each flip as a deliberate
decision, not a noisy failure.

## Cost note

Eval suite runs in <100ms on the bundled fixture. No external calls,
no LLM calls, no cost. Run on every PR.
