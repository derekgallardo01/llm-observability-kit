"""CLI - inspect, aggregate, alert-check, and demo trace files.

Usage:
    llm-obs aggregate <jsonl-path>           # one-window summary
    llm-obs windows <jsonl-path> --minutes 10   # time-bucketed stats
    llm-obs alerts <jsonl-path>                 # check default rules
    llm-obs alerts <jsonl-path> --rules custom.json
    llm-obs demo                                # everything against bundled fixture
    llm-obs record-sample                       # produces ~5 synthetic traces (via @trace)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .aggregator import aggregate, window_stats
from .alerts import AlertRule, check_alerts, default_rules
from .tracer import (
    JsonlFileSink, Trace, configure, load_traces, trace, write_traces,
)


DEFAULT_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "production-1hour.jsonl"


def cmd_aggregate(args) -> int:
    traces = load_traces(args.path)
    if not traces:
        print(f"No traces found at {args.path}")
        return 1
    agg = aggregate(traces)
    if args.json:
        print(json.dumps(agg, indent=2))
        return 0
    print(f"\nAggregate over {len(traces)} traces:")
    print(f"  Total cost:    ${agg['total_cost_usd']:.4f}")
    print(f"  Total errors:  {agg['total_errors']}")
    print(f"\n  Per feature:")
    for feature, data in sorted(agg["by_feature"].items()):
        print(f"    {feature:35s}  count={data['count']:4d}  "
              f"errors={data['error_count']:2d}  "
              f"p95={data['p95_latency_ms']:>5d}ms  "
              f"cost=${data['cost']:.4f}")
    print(f"\n  Per model:")
    for model, data in sorted(agg["by_model"].items()):
        print(f"    {model:30s}  count={data['count']:4d}  cost=${data['cost']:.4f}")
    print(f"\n  Per tenant:")
    for tenant, data in sorted(agg["by_tenant"].items()):
        print(f"    {tenant:20s}  count={data['count']:4d}  cost=${data['cost']:.4f}")
    return 0


def cmd_windows(args) -> int:
    traces = load_traces(args.path)
    if not traces:
        print(f"No traces found at {args.path}")
        return 1
    stats = window_stats(traces, window_minutes=args.minutes)
    if args.json:
        print(json.dumps([asdict(s) for s in stats], indent=2))
        return 0
    print(f"\nTime-bucketed stats ({args.minutes}-minute windows):\n")
    print(f"  {'window_start':28s}  {'feature':35s}  {'count':>5s}  {'p95ms':>6s}  {'errors':>6s}  {'cost':>8s}")
    for s in stats:
        print(f"  {s.window_start:28s}  {s.feature:35s}  "
              f"{s.count:>5d}  {s.p95_latency_ms:>6d}  "
              f"{s.error_count:>6d}  ${s.total_cost_usd:>7.4f}")
    return 0


def cmd_alerts(args) -> int:
    traces = load_traces(args.path)
    if not traces:
        print(f"No traces found at {args.path}")
        return 1
    agg = aggregate(traces)
    rules = load_rules(args.rules) if args.rules else default_rules()
    fired = check_alerts(agg, rules)
    if args.json:
        print(json.dumps({
            "checked_rules": len(rules),
            "fired": [asdict(a) for a in fired],
        }, indent=2))
        return 0 if not fired else 1
    print(f"\nChecked {len(rules)} rules; {len(fired)} fired:")
    if not fired:
        print("  No alerts. All clear.")
        return 0
    for a in fired:
        print(f"\n  ALERT: {a.rule_name}")
        print(f"    {a.description}")
        print(f"    metric:   {a.metric}")
        print(f"    actual:   {a.actual_value:.4f}")
        print(f"    threshold: {a.op} {a.threshold:.4f}")
        if a.scope:
            print(f"    scope:    {a.scope}")
    return 1


def load_rules(path: str) -> list[AlertRule]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [AlertRule(**r) for r in data.get("rules", [])]


def cmd_record_sample(args) -> int:
    """Run a few @trace-decorated calls; emit traces to stderr + a file."""
    sink_path = Path(args.out or "sample-traces.jsonl")
    configure(sinks=[JsonlFileSink(sink_path)])

    @trace(feature="customer_complaint_classifier", model="claude-haiku-4-5", tenant="acme")
    def classify(prompt: str) -> str:
        return "refund_request"

    @trace(feature="policy_summarizer", model="claude-sonnet-4-6", tenant="acme")
    def summarize(prompt: str) -> dict:
        return {"text": "EU data residency policy.", "input_tokens": 1200, "output_tokens": 200}

    @trace(feature="research_agent", model="claude-opus-4-7", tenant="acme")
    def research(prompt: str) -> str:
        # Simulate a slow call
        import time
        time.sleep(0.05)
        return "Research summary here."

    classify(prompt="I want my money back.")
    classify(prompt="Where is my order?")
    summarize(prompt="Long policy text...")
    research(prompt="Investigate vendor X.")

    # Force an error to demo error-trace handling
    @trace(feature="customer_complaint_classifier", model="claude-haiku-4-5")
    def failing(prompt: str) -> str:
        raise RuntimeError("RateLimitError: 429")

    try:
        failing(prompt="trigger error")
    except RuntimeError:
        pass

    print(f"Wrote 5 sample traces to {sink_path}")
    return 0


def cmd_demo(args) -> int:
    path = DEFAULT_FIXTURE
    if not path.exists():
        print(f"Bundled fixture missing at {path}. Run: python fixtures/generate.py")
        return 1
    print(f"Demo: bundled production-1hour.jsonl ({path})\n")

    args.path = str(path)
    args.json = False
    args.minutes = 10
    args.rules = None

    cmd_aggregate(args)
    print("\n" + "=" * 60)
    print("\nSliding-window stats:")
    cmd_windows(args)
    print("\n" + "=" * 60)
    print("\nAlert evaluation against default rules:")
    cmd_alerts(args)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LLM observability CLI.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_agg = sub.add_parser("aggregate")
    p_agg.add_argument("path")
    p_agg.add_argument("--json", action="store_true")

    p_win = sub.add_parser("windows")
    p_win.add_argument("path")
    p_win.add_argument("--minutes", type=int, default=10)
    p_win.add_argument("--json", action="store_true")

    p_alert = sub.add_parser("alerts")
    p_alert.add_argument("path")
    p_alert.add_argument("--rules", default=None,
                          help="Path to custom rules JSON. Default: bundled rules.")
    p_alert.add_argument("--json", action="store_true")

    p_rec = sub.add_parser("record-sample")
    p_rec.add_argument("--out", default=None)

    sub.add_parser("demo")

    args = parser.parse_args(argv)
    handlers = {"aggregate": cmd_aggregate, "windows": cmd_windows,
                "alerts": cmd_alerts, "record-sample": cmd_record_sample,
                "demo": cmd_demo}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
