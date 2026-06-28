# Diagrams

GitHub renders Mermaid natively. These render on the README and here.

## End-to-end flow

```mermaid
flowchart LR
    APP["@trace-wrapped LLM call"] --> CAPTURE[Capture: ts, latency, tokens, cost, error]
    CAPTURE --> R[Recorder]
    R --> S1[StderrSink<br/>JSONL to stderr]
    R --> S2[JsonlFileSink<br/>append to file]
    R -. "production" .-> S3[HTTP POST sink<br/>Datadog / OTLP / custom]
    S2 --> JL[Trace log on disk]
    JL --> A["aggregate() / window_stats()"]
    A --> AG[Per-feature/model/tenant<br/>+ percentiles + cost]
    AG --> AL["check_alerts(rules)"]
    AL --> FIRE[Fired alerts → on-call]
    AG --> DASH[Dashboard rendering]
```

## The @trace decorator

```mermaid
sequenceDiagram
    participant App
    participant Wrapper as @trace wrapper
    participant Fn as wrapped fn
    participant Rec as Recorder
    participant Sinks

    App->>Wrapper: call(prompt="...")
    Wrapper->>Wrapper: t0 = now()
    Wrapper->>Fn: call (try)
    alt success
        Fn-->>Wrapper: response
        Wrapper->>Wrapper: extract tokens (dict or estimate)
    else exception
        Fn-->>Wrapper: raises
        Wrapper->>Wrapper: error = str(ex)
    end
    Wrapper->>Wrapper: build Trace (latency = now - t0)
    Wrapper->>Rec: emit(trace)
    Rec->>Sinks: emit to each (sink errors swallowed)
    alt was error
        Wrapper-->>App: re-raise RuntimeError
    else success
        Wrapper-->>App: response
    end
```

## Aggregator

```mermaid
flowchart TB
    T[list of Traces] --> A1[Loop through traces]
    A1 --> A2[Update by_feature counters]
    A1 --> A3[Update by_model counters]
    A1 --> A4[Update by_tenant counters]
    A1 --> A5[Collect latencies per feature]
    A2 --> A6[Compute p95 per feature from sorted latencies]
    A6 --> AG[Aggregate dict]
    A3 --> AG
    A4 --> AG
    A5 --> A6
```

## Sliding-window stats

```mermaid
flowchart TB
    T[list of Traces] --> S1[Sort by timestamp]
    S1 --> S2[Build N-minute windows<br/>from first ts to last ts]
    S2 --> S3[Bucket each trace into<br/>(window_idx, feature, model)]
    S3 --> S4[Per bucket: count, p50/p95/p99,<br/>error_count, cost, tokens]
    S4 --> W[list of WindowStats]
```

## Alert evaluation

```mermaid
flowchart TB
    A[Aggregate dict] --> R[Loop through rules]
    R --> RS{Rule scope?}
    RS -- "global" --> M1[Look up global metric<br/>e.g., total_cost_usd]
    RS -- "feature=X" --> M2[Look up feature metric<br/>e.g., by_feature.X.p95_latency_ms]
    RS -- "model=X" --> M3[Look up model metric<br/>e.g., by_model.X.cost]
    M1 --> C[Compare with op + threshold]
    M2 --> C
    M3 --> C
    C -- "fires" --> F[Append Alert]
    C -- "does not fire" --> Skip[Skip]
    F --> Out[list of fired Alerts]
    Skip --> R
```

## Component responsibilities

```mermaid
flowchart TB
    subgraph Tracer["tracer.py"]
        direction TB
        T1[Trace dataclass]
        T2["@trace decorator"]
        T3[Sink protocol + Stderr/Jsonl/Collecting sinks]
        T4[Recorder dispatches Trace to all sinks]
        T5[Cost lookup table]
    end

    subgraph Aggregator["aggregator.py"]
        direction TB
        A1[aggregate: single-window snapshot]
        A2[window_stats: time-bucketed]
        A3[_percentile: nearest-rank]
    end

    subgraph Alerts["alerts.py"]
        direction TB
        AL1[AlertRule dataclass]
        AL2[Alert dataclass]
        AL3[check_alerts: rule engine]
        AL4[default_rules: starter set]
    end

    Tracer --> Aggregator
    Aggregator --> Alerts
```

## Repo shape

```mermaid
flowchart TB
    R[llm-observability-kit]
    R --> SRC[src/llm_observability/]
    SRC --> S1[tracer.py — Trace + decorator + sinks]
    SRC --> S2[aggregator.py — aggregate + window_stats]
    SRC --> S3[alerts.py — rules + check_alerts]
    SRC --> S4[cli.py — aggregate/windows/alerts/record-sample/demo]
    R --> FX[fixtures/]
    FX --> FG[generate.py — deterministic generator]
    FX --> FJ[production-1hour.jsonl — 646 bundled traces]
    R --> T[tests/]
    T --> T1[test_tracer.py — 10 tests]
    T --> T2[test_aggregator.py — 11 tests]
    T --> T3[test_alerts.py — 10 tests]
    R --> EV[evals/]
    EV --> EG[golden.json — 5 cases]
    EV --> ER[run.py — path-based assertion harness]
    R --> DOCS[docs/]
    R --> CI[.github/workflows/ci.yml]
    R --> DK[Dockerfile]
```
