"""Scheduled alert evaluator that posts fired alerts to Slack.

Run as a 15-min cron job / GitHub Actions schedule. Reads the trace log,
aggregates, checks rules, posts any fired alerts as Slack messages with
rich context (rule name, actual value, threshold, scope).

By default it operates in DRY-RUN mode (prints the Slack payload to
stdout). Set SLACK_WEBHOOK_URL to actually post.

Usage:
    python examples/slack_alerter.py
    SLACK_WEBHOOK_URL=https://hooks.slack.com/... python examples/slack_alerter.py
    python examples/slack_alerter.py --traces /var/log/llm-traces.jsonl --rules rules.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from dataclasses import asdict
from pathlib import Path

# Make the package importable when running from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from llm_observability import aggregate, check_alerts, AlertRule, Alert  # noqa: E402
from llm_observability.alerts import default_rules  # noqa: E402
from llm_observability.tracer import load_traces  # noqa: E402


DEFAULT_TRACES = Path(__file__).resolve().parents[1] / "fixtures" / "production-1hour.jsonl"


def build_slack_payload(alerts: list[Alert]) -> dict:
    """Build a Slack Block Kit message for the fired alerts."""
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"🚨 {len(alerts)} LLM alert(s) fired"}},
    ]
    for a in alerts:
        scope_str = ", ".join(f"{k}={v}" for k, v in a.scope.items()) if a.scope else "global"
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{a.rule_name}* — _{scope_str}_\n"
                    f"{a.description}\n"
                    f">*Actual:* `{a.actual_value:.4f}`   "
                    f"*Threshold:* `{a.op} {a.threshold:.4f}`"
                ),
            },
        })
        blocks.append({"type": "divider"})
    return {"text": f"{len(alerts)} LLM alert(s) fired", "blocks": blocks}


def post_to_slack(webhook_url: str, payload: dict) -> int:
    """POST the payload to Slack. Returns HTTP status code."""
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status


def load_rules_from_file(path: str) -> list[AlertRule]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [AlertRule(**r) for r in data.get("rules", [])]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scheduled LLM alert -> Slack dispatcher.")
    parser.add_argument("--traces", default=str(DEFAULT_TRACES),
                        help="Path to JSONL trace file.")
    parser.add_argument("--rules", default=None,
                        help="Optional path to custom rules JSON. Default: bundled rules.")
    parser.add_argument("--webhook", default=os.environ.get("SLACK_WEBHOOK_URL", ""),
                        help="Slack webhook URL (or SLACK_WEBHOOK_URL env var).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the Slack payload instead of posting.")
    args = parser.parse_args(argv)

    traces = load_traces(args.traces)
    if not traces:
        print(f"No traces found at {args.traces}", file=sys.stderr)
        return 1

    rules = load_rules_from_file(args.rules) if args.rules else default_rules()
    agg = aggregate(traces)
    fired = check_alerts(agg, rules)

    print(f"Checked {len(rules)} rules against {len(traces)} traces. {len(fired)} fired.")

    if not fired:
        return 0

    payload = build_slack_payload(fired)

    dry_run = args.dry_run or not args.webhook
    if dry_run:
        print("\n[DRY-RUN] Slack payload that WOULD be posted:\n")
        print(json.dumps(payload, indent=2))
        return 0

    print(f"\nPosting to Slack webhook...")
    status = post_to_slack(args.webhook, payload)
    print(f"Slack response: {status}")
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    sys.exit(main())
