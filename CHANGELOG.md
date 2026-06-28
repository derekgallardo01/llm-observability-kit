# Changelog

Notable changes to the LLM observability kit. Dates are when the
change landed on `main`.

## 2026-06-28 — Initial public release (v1.0.0)
- `tracer.py` — `Trace` dataclass; `@trace` decorator that wraps any
  LLM call; sinks (StderrSink, JsonlFileSink, CollectingSink);
  Recorder dispatcher with swallow-sink-errors guarantee; default
  pricing table covering Claude + OpenAI models
- `aggregator.py` — `aggregate()` for single-window per-feature /
  per-model / per-tenant breakdowns with p95 latency; `window_stats()`
  for time-bucketed p50/p95/p99 + cost + errors
- `alerts.py` — `AlertRule` (metric + op + threshold scoped by
  feature/model/tenant); `check_alerts()` rule engine; `default_rules()`
  starter set covering common production patterns (cost spike,
  latency regression, error rate, opus cost)
- `cli.py` — `aggregate / windows / alerts / record-sample / demo`
  subcommands with `--json` output
- Deterministic fixture generator (`fixtures/generate.py`) producing
  ~646 traces (3 features × 3 models × 2 tenants) representing 1 hour
  of synthetic production traffic
- 31 pytest tests (10 tracer + 11 aggregator + 10 alerts)
- 5 golden eval cases asserting exact properties of the bundled
  fixture (regression net for aggregator math + alert rule engine)
- CI gates on 100% tests + 100% eval pass
- CI on Python 3.10/3.11/3.12
- `pyproject.toml` with `[http]` optional extra for `httpx`
- Docs trio: `getting-started`, `architecture`, `customization`,
  `evaluation`, `diagrams`, `faq`
- OSS niceties: `CONTRIBUTING`, `CODE_OF_CONDUCT`, `SECURITY`,
  `CITATION.cff`, `.editorconfig`, `.devcontainer/devcontainer.json`,
  `.github/ISSUE_TEMPLATE/*`, `.github/PULL_REQUEST_TEMPLATE.md`,
  `.github/dependabot.yml`
- `Dockerfile`, `pages.yml` (live dashboard: summary cards + alert
  list + per-feature/model tables + sliding-window stats),
  `screenshots.yml`, `portfolio.yml` — workflows include
  `git pull --rebase` before push (race-condition fix) AND avoid
  backslash escapes in f-string expressions (Python parser limitation
  caught on graph-automation-scripts)
- README badges: CI + License (MIT) + Python (3.10+) + Open in
  Codespaces
- Theme: emerald (observability/monitoring)
