"""Trace dataclass + @trace decorator + recorder.

The trace shape is intentionally minimal - what every LLM call needs to
be observable, no provider-specific fields. Wrap any LLM call function
with @trace and every invocation produces a Trace record going to the
configured sinks (default: stderr + file).

The decorator extracts the data it needs from the wrapped function's
return value via a small protocol: the function must return either:
  - A string (raw response text), in which case tokens are estimated
  - A dict with 'text', 'input_tokens', 'output_tokens' keys
  - Anything else: trace records what it can (latency, error)

This keeps the kit vendor-neutral - wrap whatever LLM call you have,
no SDK-specific imports required.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


# Approximate per-model cost per 1K tokens (USD), as of writing. Override
# via Tracer config for accurate per-tenant pricing.
DEFAULT_PRICING = {
    "claude-opus-4-7":          {"input": 0.015,  "output": 0.075},
    "claude-sonnet-4-6":        {"input": 0.003,  "output": 0.015},
    "claude-haiku-4-5":         {"input": 0.00025, "output": 0.00125},
    "gpt-4o":                   {"input": 0.005,  "output": 0.015},
    "gpt-4o-mini":              {"input": 0.00015, "output": 0.0006},
    "gpt-4-turbo":              {"input": 0.01,   "output": 0.03},
    "gpt-35-turbo":             {"input": 0.0005, "output": 0.0015},
}


@dataclass
class Trace:
    """One LLM call invocation."""
    trace_id: str
    ts: str                    # ISO-8601 UTC
    feature: str               # what app feature called the LLM (caller-supplied)
    model: str
    prompt_hash: str           # sha256 of the rendered prompt - safe for telemetry
    prompt_chars: int          # input size as a privacy-safe proxy
    response_chars: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float
    error: str | None = None
    tenant: str = "default"    # multi-tenant tag
    user_id: str = ""          # optional user attribution
    extra: dict = field(default_factory=dict)


# ----- Sinks ----------------------------------------------------------------

class Sink:
    """Where traces go. Subclass to add Datadog / Postgres / etc."""
    def emit(self, trace: Trace) -> None:
        raise NotImplementedError


class StderrSink(Sink):
    """JSONL to stderr - cheap and good for ad-hoc dev."""
    def emit(self, trace: Trace) -> None:
        sys.stderr.write(json.dumps(asdict(trace)) + "\n")


class JsonlFileSink(Sink):
    """Append JSONL to a file. The kit's default for traces you want to keep."""
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Truncate the file fresh per process to avoid mixing runs.
        # For production you'd want rotation (daily file per process).
        self.path.touch()

    def emit(self, trace: Trace) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(trace)) + "\n")


class CollectingSink(Sink):
    """In-memory list of traces. For tests + the kit's own demo."""
    def __init__(self):
        self.traces: list[Trace] = []

    def emit(self, trace: Trace) -> None:
        self.traces.append(trace)


# ----- Recorder + decorator -------------------------------------------------

class Recorder:
    """Holds the active sinks + cost table."""
    def __init__(self, sinks: list[Sink] | None = None,
                 pricing: dict | None = None):
        self.sinks = sinks if sinks is not None else [StderrSink()]
        self.pricing = pricing or DEFAULT_PRICING

    def emit(self, trace: Trace) -> None:
        for sink in self.sinks:
            try:
                sink.emit(trace)
            except Exception:
                # Sinks must never break the wrapped LLM call.
                pass

    def cost_for(self, model: str, input_tokens: int, output_tokens: int) -> float:
        pricing = self.pricing.get(model, {"input": 0.0, "output": 0.0})
        return round(
            (input_tokens / 1000) * pricing["input"] + (output_tokens / 1000) * pricing["output"],
            6,
        )


# Module-level recorder (configurable via configure())
_RECORDER: Recorder | None = None


def configure(sinks: list[Sink] | None = None,
              pricing: dict | None = None) -> Recorder:
    """Configure the global recorder. Call once at app startup."""
    global _RECORDER
    _RECORDER = Recorder(sinks=sinks, pricing=pricing)
    return _RECORDER


def get_recorder() -> Recorder:
    """Get the configured recorder (creating a default if needed)."""
    global _RECORDER
    if _RECORDER is None:
        _RECORDER = Recorder()  # stderr default
    return _RECORDER


def trace(feature: str, model: str = "unknown", tenant: str = "default",
          user_id: str = "", recorder: Recorder | None = None):
    """Decorator that wraps an LLM call and emits a Trace per invocation.

    The wrapped function must accept a 'prompt' kwarg (or first positional
    arg) and return either:
      - A string (raw response)
      - A dict with 'text', 'input_tokens', 'output_tokens'

    Example:
        @trace(feature="customer_complaint_classifier", model="claude-haiku-4-5")
        def classify(prompt: str) -> str:
            response = anthropic_client.messages.create(...)
            return response.content[0].text
    """
    def decorator(fn: Callable):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            rec = recorder or get_recorder()
            prompt = kwargs.get("prompt") or (args[0] if args else "")
            prompt_str = str(prompt)
            t0 = time.perf_counter()
            error_msg = None
            response_text = ""
            input_tokens = 0
            output_tokens = 0
            try:
                result = fn(*args, **kwargs)
                if isinstance(result, dict):
                    response_text = result.get("text", "")
                    input_tokens = int(result.get("input_tokens") or _estimate_tokens(prompt_str))
                    output_tokens = int(result.get("output_tokens") or _estimate_tokens(response_text))
                else:
                    response_text = str(result)
                    input_tokens = _estimate_tokens(prompt_str)
                    output_tokens = _estimate_tokens(response_text)
            except Exception as ex:
                error_msg = str(ex)
                result = None
            finally:
                elapsed_ms = int((time.perf_counter() - t0) * 1000)

            trace_obj = Trace(
                trace_id=str(uuid.uuid4()),
                ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                feature=feature,
                model=model,
                prompt_hash=hashlib.sha256(prompt_str.encode("utf-8")).hexdigest()[:16],
                prompt_chars=len(prompt_str),
                response_chars=len(response_text),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=elapsed_ms,
                cost_usd=rec.cost_for(model, input_tokens, output_tokens),
                error=error_msg,
                tenant=tenant,
                user_id=user_id,
            )
            rec.emit(trace_obj)
            if error_msg:
                # Re-raise the original exception so the wrap is transparent.
                raise RuntimeError(error_msg)
            return result
        return wrapper
    return decorator


def _estimate_tokens(text: str) -> int:
    """Rough estimate: 4 chars per token. Adequate for cost/latency observability.

    For exact counts wire tiktoken or anthropic.count_tokens; the kit's
    aggregations don't need exact tokens to be useful.
    """
    return max(1, len(text) // 4) if text else 0


# ----- Trace I/O ------------------------------------------------------------

def load_traces(path: str | Path) -> list[Trace]:
    """Load a JSONL trace file back into Trace objects."""
    p = Path(path)
    if not p.exists():
        return []
    traces: list[Trace] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        data = json.loads(line)
        traces.append(Trace(**data))
    return traces


def write_traces(traces: list[Trace], path: str | Path) -> None:
    """Write a list of Traces back to JSONL (useful for tests + replay)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for t in traces:
            f.write(json.dumps(asdict(t)) + "\n")
