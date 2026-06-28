# Walkthrough

End-to-end: from "I have LLM calls in production" to "I have a
dashboard and alerts."

## Setup

```bash
pip install -e .
```

## Step 1: Wrap your LLM call

```python
from llm_observability import trace, configure
from llm_observability.tracer import JsonlFileSink

# Configure at app startup
configure(sinks=[JsonlFileSink("/var/log/llm-traces.jsonl")])

# Wrap any LLM call with @trace
@trace(feature="customer_complaint_classifier",
       model="claude-haiku-4-5",
       tenant="acme")
def classify(prompt: str) -> str:
    response = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=16,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
```

Every `classify(prompt=...)` call now emits a Trace to the JSONL file.

## Step 2: Run it (let traces accumulate)

```bash
# Your normal app process runs; traces append to the file
# After some time, the file has hundreds/thousands of trace lines
$ wc -l /var/log/llm-traces.jsonl
4823
```

## Step 3: Aggregate to see what's happening

```bash
$ llm-obs aggregate /var/log/llm-traces.jsonl

Aggregate over 4823 traces:
  Total cost:    $12.4502
  Total errors:  47

  Per feature:
    customer_complaint_classifier        count=3820  errors=24  p95= 612ms  cost=$ 0.2451
    policy_summarizer                    count= 781  errors=15  p95=2103ms  cost=$ 4.1289
    research_agent                       count= 222  errors= 8  p95=8842ms  cost=$ 8.0762

  Per model:
    claude-haiku-4-5                count=3820  cost=$ 0.2451
    claude-opus-4-7                 count= 222  cost=$ 8.0762
    claude-sonnet-4-6               count= 781  cost=$ 4.1289

  Per tenant:
    acme                  count=3201  cost=$10.0114
    globex                count=1622  cost=$ 2.4388
```

Two seconds in, you can see:
- Total spend
- Per-feature volume + latency + cost
- Per-model cost share (Opus is 65% of cost at 5% of calls)
- Per-tenant share

## Step 4: Look at time-bucketed stats

```bash
$ llm-obs windows /var/log/llm-traces.jsonl --minutes 10

window_start              feature                        count  p95ms  errors  cost
2026-06-28T09:00:00Z      customer_complaint_classifier   650    532       3  $0.0420
2026-06-28T09:00:00Z      policy_summarizer               130   2009       2  $0.6841
2026-06-28T09:00:00Z      research_agent                   37   8540       1  $1.3494
2026-06-28T09:10:00Z      customer_complaint_classifier   668    549       4  $0.0423
...
```

Wired into a chart, you get a time-series of cost/latency/errors.

## Step 5: Check alerts

```bash
$ llm-obs alerts /var/log/llm-traces.jsonl

Checked 4 rules; 2 fired:

  ALERT: opus_cost_spike
    Opus model usage exceeded $2 in window - check whether Sonnet/Haiku would work.
    metric:    model_cost_usd
    actual:    8.0762
    threshold: > 2.0000
    scope:    {'model': 'claude-opus-4-7'}

  ALERT: classifier_latency_regression
    Classifier p95 latency exceeded 2s - investigate model or prompt size.
    metric:    p95_latency_ms
    actual:    2103.0000
    threshold: > 2000.0000
    scope:    {'feature': 'policy_summarizer'}
```

Two real issues surfaced:
1. **Opus cost** - $8 on a model burning 65% of total cost. Worth
   investigating whether Sonnet would be good enough.
2. **Policy summarizer p95** - 2.1s vs 2s SLA. Marginal but trending.

## Step 6: Wire alerts to your on-call

```python
# scheduled job, runs every 15 minutes via cron / GitHub Actions
from llm_observability import aggregate, check_alerts, default_rules
from llm_observability.tracer import load_traces
import requests

traces = load_traces("/var/log/llm-traces.jsonl")
agg = aggregate(traces)
fired = check_alerts(agg, default_rules())

for alert in fired:
    requests.post("https://hooks.slack.com/services/...", json={
        "channel": "#llm-oncall",
        "text": f"[{alert.rule_name}] {alert.description}",
        "attachments": [{
            "color": "warning",
            "fields": [
                {"title": "Actual", "value": f"{alert.actual_value:.4f}", "short": True},
                {"title": "Threshold", "value": f"{alert.op} {alert.threshold}", "short": True},
                {"title": "Scope", "value": str(alert.scope), "short": False},
            ],
        }],
    })
```

Now on-call gets Slack notifications when thresholds breach. The kit
gives you the **what**; your dispatcher gives the **how**.

## Step 7: Customize the rules per environment

```python
# In your alerts config (different per env):
DEV_RULES = [
    AlertRule(name="any_cost", metric="total_cost_usd", op=">", threshold=0.5),
]

PROD_RULES = [
    AlertRule(name="hourly_budget", metric="total_cost_usd", op=">", threshold=50.0),
    AlertRule(name="critical_path_p95", metric="p95_latency_ms", op=">", threshold=1500,
              feature="customer_facing_classifier"),
    AlertRule(name="enterprise_tenant_errors", metric="error_rate", op=">",
              threshold=0.01, tenant="enterprise_acme"),
]

rules = PROD_RULES if env == "production" else DEV_RULES
```

## Step 8: Ship to a proper TSDB when volume demands

Once you outgrow JSONL files (~10M traces/day = ~5GB/day), wire an
HTTP sink to your real observability platform:

```python
from llm_observability.tracer import configure, JsonlFileSink
from my_company.sinks import DatadogSink

configure(sinks=[
    JsonlFileSink("/var/log/llm-traces-recent.jsonl"),  # local debug
    DatadogSink(api_key=os.environ["DD_API_KEY"]),       # long-term + dashboards
])
```

The aggregator + alerts still work against the local JSONL for
debugging. Datadog handles the long-term storage + UI.

## The whole loop, one diagram

```
@trace-wrapped LLM call
    → emit Trace to sinks (stderr + jsonl + your http sink)
        → JSONL accumulates on disk
            → scheduled cron job (every 15 min):
                → load_traces(jsonl)
                    → aggregate()
                        → check_alerts(default_rules + your_rules)
                            → fire to Slack/PagerDuty/email
            → on-call investigates
                → either fix the regression (prompt, model, infra)
                  or update thresholds
```

That's runtime LLM observability. You build this loop once per
engagement. The kit is the substrate.
