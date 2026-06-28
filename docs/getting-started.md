# Getting started

Five minutes to wrapping your first LLM call with observability.

## Install

```bash
git clone https://github.com/derekgallardo01/llm-observability-kit.git
cd llm-observability-kit
pip install -e .
```

Stdlib-only on the default path. `pip install -e ".[http]"` adds
`httpx` for the HTTP-POST sink.

## Run the demo

```bash
llm-obs demo
```

Loads the bundled 1-hour fixture (646 traces), aggregates, computes
sliding-window stats, and runs the default alert rules. Output shows
the full dashboard.

## Wrap your own LLM call

```python
from llm_observability import trace, configure
from llm_observability.tracer import JsonlFileSink

# Configure once at app startup
configure(sinks=[
    JsonlFileSink("/var/log/llm-traces.jsonl"),
])

@trace(feature="my_classifier", model="claude-haiku-4-5", tenant="acme")
def classify(prompt: str) -> str:
    # Your LLM call here
    return llm_client.chat(prompt=prompt)

# Every call now emits a Trace
result = classify(prompt="some message")
```

For richer token counts, return a dict from the wrapped function:

```python
@trace(feature="my_classifier", model="claude-haiku-4-5")
def classify(prompt: str) -> dict:
    response = anthropic_client.messages.create(...)
    return {
        "text": response.content[0].text,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
```

The kit uses your token counts when provided; otherwise estimates
from text length (4 chars/token).

## Inspect captured traces

```bash
llm-obs aggregate /var/log/llm-traces.jsonl
llm-obs windows /var/log/llm-traces.jsonl --minutes 10
llm-obs alerts /var/log/llm-traces.jsonl
```

Add `--json` for machine-readable output.

## Wire to your alerting

```python
from llm_observability import aggregate, check_alerts, AlertRule
from llm_observability.tracer import load_traces

# Run as a cron job every 15 minutes
traces = load_traces("/var/log/llm-traces.jsonl")
agg = aggregate(traces)

rules = [
    AlertRule(name="cost_spike", metric="total_cost_usd", op=">", threshold=10.0),
    AlertRule(name="classifier_slow",
              metric="p95_latency_ms", op=">", threshold=2000,
              feature="my_classifier"),
]

for alert in check_alerts(agg, rules):
    slack_notify(channel="#oncall",
                 message=f"[{alert.rule_name}] {alert.description} - actual: {alert.actual_value}")
```

## Run the tests

```bash
python -m pytest -q
```

31 tests across tracer, aggregator, and alerts.

## Run the evals

```bash
python evals/run.py
```

5 cases against the bundled fixture (the deterministic
production-1hour.jsonl). CI gates on 100%.

## Generate your own fixture

```bash
python fixtures/generate.py
```

Edit the `make_feature_config()` function in `fixtures/generate.py`
to tune the synthetic workload (call rates, model mix, latency
distributions, error rates).

## Next steps

- [Architecture](architecture.md) — tracer + aggregator + alerts design
- [Customization](customization.md) — add sinks, rules, metrics
- [Evaluation](evaluation.md) — eval cases against the fixture
