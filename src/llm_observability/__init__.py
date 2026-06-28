"""Runtime observability for LLM calls.

Default sinks are stderr (JSONL) + file (JSONL) - no network dependency.
Set LLM_OBS_HTTP_SINK_URL to add HTTP POST shipping to a real sink
(Datadog, OpenTelemetry collector, custom endpoint).
"""
from .tracer import Trace, trace, get_recorder
from .aggregator import aggregate, window_stats
from .alerts import Alert, AlertRule, check_alerts

__version__ = "1.0.0"
__all__ = [
    "Trace", "trace", "get_recorder",
    "aggregate", "window_stats",
    "Alert", "AlertRule", "check_alerts",
]
