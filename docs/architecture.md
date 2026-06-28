# Architecture

Three components with clean responsibility boundaries:

1. **Tracer** (`tracer.py`) — wraps LLM calls with `@trace`, builds
   `Trace` records, emits to configured sinks.
2. **Aggregator** (`aggregator.py`) — reads traces, computes
   sliding-window stats + per-dimension breakdowns.
3. **Alerts** (`alerts.py`) — evaluates rules against aggregates,
   returns fired alerts.

## End-to-end flow

```
@trace-wrapped function called
    -> Tracer captures: trace_id, ts, feature, model, prompt_hash,
                        prompt_chars, response_chars, input_tokens,
                        output_tokens, latency_ms, cost_usd, error
    -> Recorder dispatches Trace to every configured Sink
        -> StderrSink: JSON line to stderr
        -> JsonlFileSink: append JSON line to file
        -> (your custom Sink: ship to Datadog / OTLP / Postgres)

(later, on demand or scheduled)

load_traces(jsonl_path) -> list[Trace]
    -> aggregate(traces) -> dict (per-feature, per-model, per-tenant)
    -> window_stats(traces, minutes=N) -> list[WindowStats]
    -> check_alerts(agg, rules) -> list[Alert]
```

## Why JSONL on disk by default

Three reasons the kit defaults to file-based traces instead of a
real time-series DB:

1. **Zero infrastructure to start.** Drop the kit into your code,
   point at a file path, done. No Postgres, no InfluxDB, no Datadog
   account.
2. **Locally inspectable.** Open the file in your editor; it's
   readable JSON, one trace per line.
3. **Easy to ship later.** `cat traces.jsonl | curl -d @- ...` to any
   collector. Or replace the sink with your HTTP shipper.

For high-volume production (thousands of calls per second), wire an
HTTP sink to your real observability platform. The aggregator code
works against any source - file, Postgres, ClickHouse - because it
takes a `list[Trace]`.

## The decorator's contract

```python
@trace(feature="my_feature", model="my_model", tenant="t")
def llm_call(prompt: str) -> str | dict:
    ...
```

Two return shapes supported:
- **`str`**: the response text. Token counts estimated as
  `len(text) // 4`.
- **`dict`**: `{"text": "...", "input_tokens": N, "output_tokens": M}`.
  Real token counts used.

If the wrapped function raises, the exception is re-raised after the
Trace is emitted (with `error` set). So:
- Your callers' error handling is unaffected
- The trace captures the failure for the dashboard
- Sink errors NEVER break the LLM call

## Why a fixed cost table

`DEFAULT_PRICING` in `tracer.py` is a snapshot of current per-1K-token
pricing for common models. For production:

- Override via `configure(pricing=your_table)` at startup
- For Azure OpenAI's regional pricing variation, see
  [azure-openai-evals](https://github.com/derekgallardo01/azure-openai-evals)
- For accurate billing reconciliation, capture real `usage` data from
  the SDK and compute cost downstream

The kit's cost field is good for trending and threshold alerts. Not
for final billing.

## Aggregation math

The kit's `_percentile` is a simple nearest-rank percentile:

```python
def _percentile(sorted_values, pct):
    idx = max(0, int(len(sorted_values) * pct / 100) - 1)
    return sorted_values[idx]
```

For 100 values, p95 = value at index 94 = 95th smallest. This matches
"95% of samples are at or below this value" within the precision the
nearest-rank method gives.

For sub-percentile precision (interpolated percentiles, t-digest),
swap with `numpy.percentile` or `tdigest` - the aggregator interface
doesn't change.

## Sliding windows

`window_stats(traces, window_minutes=N)`:

1. Sort traces by timestamp
2. Build N-minute window buckets from first trace to last
3. For each (window, feature, model) bucket, compute count, error
   count, p50/p95/p99, total cost, total tokens

Returns one `WindowStats` per (window, feature, model) - the shape
your dashboard's time-series chart wants.

## Alert evaluation

Each `AlertRule` is `(metric, op, threshold)` optionally scoped by
feature/model/tenant. The evaluator:

1. Looks up the metric value from the aggregate (scoped per rule)
2. Compares against threshold with the rule's operator
3. Returns an `Alert` if the rule fires

Metrics the alert engine understands:
- `total_cost_usd` — across all calls
- `feature_cost_usd` — scoped to a feature
- `model_cost_usd` — scoped to a model
- `p95_latency_ms` / `p99_latency_ms` — scoped to a feature
- `error_rate` — global or per-feature
- `count` / `feature_count` — call volume

For additional metrics (per-tenant cost, per-user latency), extend
`_resolve_metric` in `alerts.py` to look up the new path.

## Why this isn't OpenTelemetry-compatible

OpenTelemetry's span shape is much richer than `Trace` (multiple
events per span, attributes, context propagation). The kit's shape
is intentionally minimal for LLM-specific observability.

To bridge: write an `OtelSink` that converts `Trace` to an OTel span
before exporting. ~20 lines.

The kit's value over raw OTel for LLM workloads:

- LLM-specific fields out of the box (prompt_hash, cost_usd, model)
- LLM-tuned aggregator (per-model cost, per-feature p95)
- LLM-tuned default alert rules

OTel gives you the general-purpose collector + storage. This kit
gives you the LLM-specific math + UX. Most production deployments
end up with both.

## Sinks must never break the wrapped call

The `Recorder.emit` method catches sink exceptions:

```python
def emit(self, trace):
    for sink in self.sinks:
        try:
            sink.emit(trace)
        except Exception:
            pass  # silent - sink errors must not propagate
```

This is intentional. If your Datadog HTTP sink fails, the customer's
LLM call must still complete. Loud sink failures are anti-feature
in production.

For sink-error visibility, write sink errors to your own logger
inside the sink's `emit`:

```python
class HttpSink(Sink):
    def emit(self, trace):
        try:
            self.client.post(self.url, json=asdict(trace))
        except Exception as e:
            local_logger.warning(f"trace ship failed: {e}")
            raise
```

Recorder still swallows. Your logger captures.

## What's deliberately NOT in the kit

- **Distributed tracing context propagation** — that's OpenTelemetry's
  job. Wrap the kit in OTel if you need it.
- **Real-time alerting transport** — `check_alerts` returns a list;
  your code dispatches to Slack/PagerDuty/email.
- **Long-term storage** — JSONL works for hours-to-days; for months
  of retention ship to a real TSDB.
- **Multi-language support** — Python only. For Node/Go/Java, port
  the `@trace` decorator pattern to your language.
