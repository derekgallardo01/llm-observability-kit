"""Eval harness - runs aggregate + alert assertions against the bundled fixture."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from llm_observability.aggregator import aggregate  # noqa: E402
from llm_observability.alerts import check_alerts, default_rules  # noqa: E402
from llm_observability.tracer import load_traces  # noqa: E402


FIXTURES = ROOT / "fixtures"


def load_cases() -> list[dict]:
    with open(Path(__file__).parent / "golden.json") as f:
        return json.load(f)["cases"]


def resolve_path(data, path: str):
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def evaluate_assertion(data, assertion: dict) -> tuple[bool, str]:
    path = assertion["path"]
    op = assertion["op"]
    expected = assertion["value"]
    actual = resolve_path(data, path)

    if actual is None:
        return False, f"{path}=None"

    if op == "==":
        ok = actual == expected
    elif op == ">=":
        ok = actual >= expected
    elif op == "<=":
        ok = actual <= expected
    elif op == ">":
        ok = actual > expected
    elif op == "<":
        ok = actual < expected
    else:
        return False, f"unknown op: {op}"

    return ok, f"{path}={actual} (expected {op} {expected})"


def run_aggregate_case(case: dict) -> tuple[bool, list[str]]:
    traces = load_traces(FIXTURES / case["fixture"])
    agg = aggregate(traces)
    details = []
    all_ok = True
    for assertion in case["assertions"]:
        ok, detail = evaluate_assertion(agg, assertion)
        if not ok:
            all_ok = False
        details.append(("OK   " if ok else "FAIL ") + detail)
    return all_ok, details


def run_alert_case(case: dict) -> tuple[bool, list[str]]:
    traces = load_traces(FIXTURES / case["fixture"])
    agg = aggregate(traces)
    fired = check_alerts(agg, default_rules())
    fired_names = {f.rule_name for f in fired}
    rule = case["rule_name"]
    expected_fired = case["expected_fired"]
    actually_fired = rule in fired_names
    ok = actually_fired == expected_fired
    detail = (f"rule '{rule}': "
              f"{'fired' if actually_fired else 'did not fire'} "
              f"(expected {'fired' if expected_fired else 'did not fire'})")
    return ok, [("OK   " if ok else "FAIL ") + detail]


def main() -> int:
    cases = load_cases()
    print(f"Running {len(cases)} eval cases against backend=stub\n")

    total_passed = 0
    for case in cases:
        if case["type"] == "aggregate":
            passed, details = run_aggregate_case(case)
        elif case["type"] == "alert":
            passed, details = run_alert_case(case)
        else:
            passed, details = False, [f"unknown case type: {case['type']}"]

        status = "PASS" if passed else "FAIL"
        print(f"  {status}  {case['id']}")
        for d in details:
            print(f"        {d}")
        if passed:
            total_passed += 1

    print(f"\n  {total_passed}/{len(cases)} cases passed ({total_passed / len(cases):.0%})")
    return 0 if total_passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
