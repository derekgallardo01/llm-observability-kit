"""Tests for the @trace decorator + Trace dataclass + Recorder."""

import json
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from llm_observability.tracer import (  # noqa: E402
    CollectingSink, JsonlFileSink, Recorder, Trace, configure,
    get_recorder, load_traces, trace, write_traces, DEFAULT_PRICING,
)


def test_trace_dataclass_has_expected_fields():
    t = Trace(
        trace_id="x", ts="2026-06-28T12:00:00.000Z",
        feature="f", model="claude-haiku-4-5",
        prompt_hash="h", prompt_chars=100, response_chars=20,
        input_tokens=25, output_tokens=5, latency_ms=300,
        cost_usd=0.0001,
    )
    assert t.feature == "f"
    assert t.error is None
    assert t.tenant == "default"


def test_recorder_emits_to_all_sinks():
    sink1 = CollectingSink()
    sink2 = CollectingSink()
    rec = Recorder(sinks=[sink1, sink2])

    @trace(feature="t", model="claude-haiku-4-5", recorder=rec)
    def f(prompt: str) -> str:
        return "ok"

    f(prompt="hello")
    assert len(sink1.traces) == 1
    assert len(sink2.traces) == 1
    assert sink1.traces[0].feature == "t"


def test_decorator_captures_string_response():
    sink = CollectingSink()
    rec = Recorder(sinks=[sink])

    @trace(feature="f", model="claude-haiku-4-5", recorder=rec)
    def my_call(prompt: str) -> str:
        return "the response"

    my_call(prompt="my prompt here")
    t = sink.traces[0]
    assert t.response_chars == len("the response")
    assert t.input_tokens > 0
    assert t.output_tokens > 0
    assert t.cost_usd > 0


def test_decorator_uses_dict_token_counts_when_provided():
    sink = CollectingSink()
    rec = Recorder(sinks=[sink])

    @trace(feature="f", model="claude-haiku-4-5", recorder=rec)
    def my_call(prompt: str) -> dict:
        return {"text": "short", "input_tokens": 1000, "output_tokens": 500}

    my_call(prompt="anything")
    t = sink.traces[0]
    # Should use the dict's counts, not the estimate
    assert t.input_tokens == 1000
    assert t.output_tokens == 500


def test_decorator_records_errors():
    sink = CollectingSink()
    rec = Recorder(sinks=[sink])

    @trace(feature="f", model="claude-haiku-4-5", recorder=rec)
    def my_call(prompt: str) -> str:
        raise ValueError("upstream failed")

    with pytest.raises(RuntimeError, match="upstream failed"):
        my_call(prompt="anything")
    # Trace should still have been recorded
    assert len(sink.traces) == 1
    assert sink.traces[0].error is not None
    assert "upstream failed" in sink.traces[0].error


def test_cost_calculated_from_pricing_table():
    sink = CollectingSink()
    rec = Recorder(sinks=[sink])

    @trace(feature="f", model="claude-haiku-4-5", recorder=rec)
    def my_call(prompt: str) -> dict:
        return {"text": "ok", "input_tokens": 1000, "output_tokens": 1000}

    my_call(prompt="anything")
    t = sink.traces[0]
    # haiku: 0.00025 input + 0.00125 output per 1K = 0.0015 total
    assert abs(t.cost_usd - 0.0015) < 1e-6


def test_jsonl_file_sink_writes_to_disk(tmp_path):
    sink_path = tmp_path / "traces.jsonl"
    sink = JsonlFileSink(sink_path)
    sink.emit(Trace(
        trace_id="x", ts="2026-06-28T12:00:00.000Z",
        feature="f", model="m", prompt_hash="h",
        prompt_chars=10, response_chars=10,
        input_tokens=10, output_tokens=10, latency_ms=100, cost_usd=0.001,
    ))
    lines = sink_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["feature"] == "f"


def test_sink_errors_dont_break_wrapped_call():
    """If a sink crashes, the LLM call must still complete."""
    class BadSink:
        def emit(self, _trace):
            raise RuntimeError("sink broken")

    sink_good = CollectingSink()
    rec = Recorder(sinks=[BadSink(), sink_good])

    @trace(feature="f", model="claude-haiku-4-5", recorder=rec)
    def my_call(prompt: str) -> str:
        return "still works"

    result = my_call(prompt="hi")
    assert result == "still works"
    assert len(sink_good.traces) == 1


def test_load_and_write_traces_roundtrip(tmp_path):
    traces = [
        Trace(trace_id=f"x{i}", ts="2026-06-28T12:00:00.000Z",
              feature="f", model="m", prompt_hash="h",
              prompt_chars=10, response_chars=10,
              input_tokens=10, output_tokens=10, latency_ms=100, cost_usd=0.001)
        for i in range(5)
    ]
    path = tmp_path / "out.jsonl"
    write_traces(traces, path)
    loaded = load_traces(path)
    assert len(loaded) == 5
    assert loaded[0].trace_id == "x0"


def test_pricing_table_includes_common_models():
    assert "claude-haiku-4-5" in DEFAULT_PRICING
    assert "gpt-4o" in DEFAULT_PRICING
    assert "input" in DEFAULT_PRICING["gpt-4o"]
    assert "output" in DEFAULT_PRICING["gpt-4o"]
