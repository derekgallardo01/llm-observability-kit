# Customization

How to shape the kit for production.

## Add an HTTP sink

```python
import httpx
from dataclasses import asdict
from llm_observability.tracer import Sink, configure, JsonlFileSink, Trace


class HttpSink(Sink):
    """Ship traces to a collector endpoint over HTTP."""
    def __init__(self, url: str, headers: dict | None = None, timeout: float = 2.0):
        self.url = url
        self.headers = headers or {}
        self.client = httpx.Client(timeout=timeout)

    def emit(self, trace: Trace) -> None:
        self.client.post(self.url, json=asdict(trace), headers=self.headers)


# Wire at startup
configure(sinks=[
    JsonlFileSink("/var/log/llm-traces.jsonl"),
    HttpSink("https://your-collector.example.com/traces",
             headers={"Authorization": "Bearer ..."}),
])
```

Sink errors are swallowed by the Recorder so your HTTP collector
going down won't break LLM calls. For visibility, log inside the
sink before raising.

## Add a Datadog sink

```python
import datadog_api_client
from llm_observability.tracer import Sink

class DatadogSink(Sink):
    def __init__(self, api_key: str):
        configuration = datadog_api_client.Configuration()
        configuration.api_key["apiKeyAuth"] = api_key
        self.api = datadog_api_client.v2.api.MetricsApi(
            datadog_api_client.ApiClient(configuration)
        )

    def emit(self, trace):
        # Ship as a metric series + an event for errors
        self.api.submit_metrics(body={
            "series": [
                {"metric": "llm.calls.count", "type": 1, "points": [[ts(), 1]],
                 "tags": [f"feature:{trace.feature}", f"model:{trace.model}",
                          f"tenant:{trace.tenant}"]},
                {"metric": "llm.calls.latency_ms", "type": 3, "points": [[ts(), trace.latency_ms]],
                 "tags": [...]},
                {"metric": "llm.calls.cost_usd", "type": 1, "points": [[ts(), trace.cost_usd]],
                 "tags": [...]},
            ]
        })
```

## Add a Postgres sink

```python
import psycopg2
from dataclasses import asdict
from llm_observability.tracer import Sink

class PostgresSink(Sink):
    def __init__(self, dsn: str):
        self.conn = psycopg2.connect(dsn)
        # Make sure the table exists
        self.conn.cursor().execute("""
            CREATE TABLE IF NOT EXISTS llm_traces (
                trace_id TEXT PRIMARY KEY,
                ts TIMESTAMPTZ,
                feature TEXT,
                model TEXT,
                latency_ms INTEGER,
                cost_usd NUMERIC(10, 6),
                error TEXT,
                tenant TEXT,
                raw JSONB
            )
        """)
        self.conn.commit()

    def emit(self, trace):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO llm_traces VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (trace.trace_id, trace.ts, trace.feature, trace.model,
             trace.latency_ms, trace.cost_usd, trace.error, trace.tenant,
             json.dumps(asdict(trace))),
        )
        self.conn.commit()
```

## Customize the pricing table

```python
from llm_observability.tracer import configure, JsonlFileSink

MY_PRICING = {
    "claude-haiku-4-5":  {"input": 0.00025, "output": 0.00125},
    "claude-sonnet-4-6": {"input": 0.003,   "output": 0.015},
    "gpt-4o":            {"input": 0.005,   "output": 0.015},
    # Custom enterprise discount
    "gpt-4o-discounted": {"input": 0.003,   "output": 0.012},
    # Local model - $0
    "local-llama":       {"input": 0.0,     "output": 0.0},
}

configure(
    sinks=[JsonlFileSink("/var/log/llm-traces.jsonl")],
    pricing=MY_PRICING,
)
```

For Azure OpenAI's per-region pricing, see
[azure-openai-evals](https://github.com/derekgallardo01/azure-openai-evals)'s
cost-projection code which handles regional variation.

## Add custom alert rules

```python
from llm_observability import AlertRule, check_alerts

MY_RULES = [
    AlertRule(name="critical_path_latency",
              metric="p95_latency_ms", op=">", threshold=3000,
              feature="critical_user_facing_classifier",
              description="Critical-path classifier p95 over 3s - user-facing impact"),

    AlertRule(name="hourly_total_budget",
              metric="total_cost_usd", op=">", threshold=20.0,
              description="Hourly LLM spend > $20 - investigate volume"),

    AlertRule(name="enterprise_tenant_error_spike",
              metric="error_rate", op=">", threshold=0.02,
              tenant="enterprise_acme",
              description="Enterprise customer hit >2% error rate - high priority"),

    AlertRule(name="opus_unjustified_use",
              metric="model_cost_usd", op=">", threshold=5.0,
              model="claude-opus-4-7",
              description="Opus spend over $5 in window - did someone forget to route to Sonnet?"),
]

# In a scheduled job (cron / GitHub Actions / Azure Functions Timer)
traces = load_traces("/var/log/llm-traces.jsonl")
agg = aggregate(traces)
for alert in check_alerts(agg, MY_RULES):
    your_alerting_system.notify(alert)
```

## Add custom metrics

To support a new metric in alerts, extend `_resolve_metric` in
`src/llm_observability/alerts.py`:

```python
def _resolve_metric(aggregate, rule):
    # ... existing branches ...

    if rule.metric == "tenant_cost_usd" and rule.tenant:
        tenant_data = aggregate.get("by_tenant", {}).get(rule.tenant)
        return float(tenant_data["cost"]) if tenant_data else None

    if rule.metric == "tokens_per_call":
        total_tokens = sum(...)  # would need raw traces, not aggregate
        return total_tokens / aggregate["total_calls"]

    return None
```

Then reference the new metric in your rules:

```python
AlertRule(name="tenant_cost",
          metric="tenant_cost_usd", op=">", threshold=100.0,
          tenant="enterprise_acme")
```

## Sample rate / drop traces

For very high-volume LLM calls (e.g., classifier called 1M times/day),
sampling reduces sink load. Wrap the recorder's `emit`:

```python
import random
from llm_observability.tracer import get_recorder, Recorder

class SamplingRecorder(Recorder):
    def __init__(self, sample_rate: float = 0.1, **kwargs):
        super().__init__(**kwargs)
        self.sample_rate = sample_rate

    def emit(self, trace):
        # Always emit errors, sample the rest
        if trace.error or random.random() < self.sample_rate:
            super().emit(trace)

# Wire at startup
configure(sinks=[...])  # set up sinks first
import llm_observability.tracer
llm_observability.tracer._RECORDER = SamplingRecorder(
    sample_rate=0.1, sinks=[JsonlFileSink("/var/log/llm-traces.jsonl")],
)
```

Errors always emit; non-error traces sampled at 10%. Aggregates
remain directionally accurate; alert thresholds need adjustment for
the sample rate.

## Persist for analytics

```python
import duckdb

# Pipe JSONL traces into DuckDB for fast analytical queries
conn = duckdb.connect("llm_traces.db")
conn.execute("""
    CREATE OR REPLACE TABLE traces AS
    SELECT * FROM read_json_auto('/var/log/llm-traces.jsonl')
""")

# Now query like a real analytics DB
conn.execute("""
    SELECT feature, model, COUNT(*) as calls, SUM(cost_usd) as cost,
           PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) as p95
    FROM traces
    WHERE ts > now() - INTERVAL '1 day'
    GROUP BY feature, model
    ORDER BY cost DESC
""").fetchall()
```

DuckDB handles millions of rows on a laptop. For long-term storage
+ querying, this is the cheapest path that's not a hosted TSDB.

## Compose with prompt-registry-kit

Tag the trace with the active prompt version so you can attribute
latency/cost regressions to specific prompt changes:

```python
from llm_observability import trace
from prompt_registry.registry import Registry

REG = Registry("./prompt-registry")

def classify(message: str):
    prompt_reg = REG.get("customer_complaint_classifier")
    active_version = prompt_reg.active()

    @trace(feature="customer_complaint_classifier",
           model=active_version.model,
           tenant=current_tenant())
    def _do_classify(prompt: str) -> str:
        return llm_client.chat(prompt=prompt)

    rendered_prompt = active_version.render(message=message)
    return _do_classify(prompt=rendered_prompt)
```

Now your traces include the model + (implicitly via prompt_hash) the
prompt version. Comparing aggregates across promotions tells you
"did v2 increase or decrease p95 latency?"
