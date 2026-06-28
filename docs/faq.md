# FAQ

## How is this different from Langfuse / Helicone / Phoenix / Promptlayer?

Those are **hosted SaaS** observability platforms. They give you:
- A web UI
- Hosted storage (you don't manage the DB)
- Multi-user collaboration
- Per-event billing

This kit is **the library underneath**. It gives you:
- Self-hosted (traces never leave your infra)
- No per-event cost
- File-on-disk format (git, grep, cat work)
- Pair with any storage / dashboard you already have

Pick based on whether you want hosted UX (use a SaaS) or
self-hosted control (use this kit). The two compose — write a custom
sink that ships to Langfuse/Helicone if you want both.

## How is this different from OpenTelemetry?

OpenTelemetry is a **general-purpose tracing standard**. Powerful,
schema-rich, well-supported across languages. Heavyweight for an
LLM-specific use case.

This kit is **LLM-specific** with a minimal schema (cost, tokens,
prompt_hash, model). Lighter to integrate, ships with LLM-tuned
aggregations + alerts out of the box.

To bridge: write an `OtelSink` that converts `Trace` to an OTel span
before exporting. ~20 lines. Then you get both.

## Why JSONL files instead of a real time-series DB?

For getting started: file-based traces work for hours-to-days of
retention, zero infrastructure. Drop the kit into your code, point
at a file, done.

For production scale: replace `JsonlFileSink` with an HTTP sink
shipping to whichever TSDB you have (Datadog, InfluxDB, ClickHouse,
Prometheus remote-write). The aggregator code works against any
source because it takes a `list[Trace]`.

The JSONL default is the "no infrastructure to start" choice. Trade
up when volume demands.

## What about sampling for high-volume calls?

Sample by wrapping the Recorder — see
[customization.md](customization.md#sample-rate--drop-traces). Errors
always emit; non-error traces sampled at your configured rate.

For 1M calls/day, 10% sampling cuts trace volume to 100k/day and
costs roughly nothing. Aggregates stay directionally accurate; alert
thresholds need adjustment for the sample rate.

## Does it work with streaming responses?

The current decorator captures total latency (start → final byte)
and total token counts. For streaming-specific metrics (time-to-first-
token, tokens/second), wrap differently:

```python
@trace(...)
def stream_call(prompt):
    response_stream = client.messages.create(stream=True, ...)
    first_byte_at = None
    full_text = ""
    for event in response_stream:
        if first_byte_at is None:
            first_byte_at = time.time()
        full_text += event.delta.text
    return {
        "text": full_text,
        "input_tokens": ...,
        "output_tokens": ...,
        # Pass time_to_first_byte via the trace's extra field
        # (requires extending the decorator to populate extra from the dict)
    }
```

For full TTFB support, add `time_to_first_byte_ms` to the `Trace`
dataclass + extract from the dict response shape. ~5 line change.

## How do I track per-user costs?

The `user_id` field is on `Trace`. Pass it via the decorator (or
extend the decorator to accept a function that reads it from request
context):

```python
@trace(feature="my_classifier", model="claude-haiku-4-5", user_id=current_user.id)
def classify(prompt):
    ...
```

Aggregator doesn't currently break down by user_id; extend `aggregate`
to add `by_user` if you need per-user metrics.

## How accurate are the cost numbers?

The kit's cost field is computed from:
- The token count (real if your wrapped function returns a dict; estimated otherwise)
- The default pricing table

For directional cost monitoring (alerts, dashboards), this is fine.
For final billing reconciliation: capture real `usage` data from the
SDK and compute cost downstream from Anthropic's / OpenAI's official
billing reports.

Common gotchas:
- Token-count estimation (4 chars/token) is rough; for accurate
  counts, use tiktoken / anthropic.count_tokens in your wrapped
  function and return a dict
- Default pricing is per-list; enterprise discounts not reflected
- Azure OpenAI has per-region variation not captured here (see
  [azure-openai-evals](https://github.com/derekgallardo01/azure-openai-evals))

## What's the right alerting cadence?

Run `check_alerts` against `aggregate` on a 5-15 minute schedule:

- Too short (1 min): alerts fire on transient blips
- Too long (1 hour): you find out about regressions an hour after
  they happen

15 minutes is the sweet spot for most teams. Cron / GitHub Actions
schedule / Azure Functions timer trigger.

## Can the kit fire alerts in real-time, per-call?

The current shape is batch (aggregate-then-alert). For per-call
alerts (e.g., "this single call took 30 seconds — page someone"),
add the check inside the Recorder:

```python
class AlertingRecorder(Recorder):
    def __init__(self, per_call_rules, **kwargs):
        super().__init__(**kwargs)
        self.per_call_rules = per_call_rules

    def emit(self, trace):
        super().emit(trace)
        for rule in self.per_call_rules:
            if matches(trace, rule):
                page_oncall(trace, rule)
```

Per-call alerts are noisier but catch tail-of-distribution issues
that aggregate alerts miss.

## How does this handle multi-tenant SaaS?

The `tenant` field is on `Trace`. Per-tenant rules and aggregates
work:

```python
@trace(feature="classifier", model="haiku", tenant=request.tenant_id)
def classify(prompt): ...
```

```python
rules = [
    AlertRule(name=f"{t}_cost", metric="tenant_cost_usd", op=">",
              threshold=tenant_budget(t), tenant=t)
    for t in active_tenants()
]
```

For per-tenant cost attribution that matches billing, capture real
token usage (not estimates) and use accurate pricing.

## Will sink failures break my LLM calls?

No. The Recorder catches sink exceptions:

```python
def emit(self, trace):
    for sink in self.sinks:
        try:
            sink.emit(trace)
        except Exception:
            pass  # silent
```

This is intentional — observability sinks must never break the LLM
call. If your Datadog HTTP sink is down, customer requests still
work; you lose the trace for those calls.

For sink-error visibility, log inside the sink before raising. The
Recorder swallows the raise; your logger captures the failure.

## How big does the JSONL file get?

Per trace: ~400-600 bytes. 1M calls/day = 400-600 MB/day. Rotate
daily and ship to cold storage.

For higher volumes, use the HTTP sink to a real TSDB; the JSONL
sink is the "getting started" default, not the "100M traces/day"
default.

## Can I record sample/synthetic traces for testing?

```bash
llm-obs record-sample --out my-traces.jsonl
```

This runs 5 @trace-decorated calls (one of each shape: string return,
dict return, error) and writes the resulting traces. Useful for
test fixtures + dashboard previews without running real LLM calls.

For larger volumes, use `fixtures/generate.py` as a template; it
produces ~600 deterministic traces from a fixed seed.
