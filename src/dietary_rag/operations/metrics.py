"""Process-local aggregate counters and durations with a fixed label vocabulary."""

import threading
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from contextvars import ContextVar

CURRENT_METRICS = ContextVar("dietary_metrics", default=None)
STAGES = frozenset(
    {"policy", "corpus_open", "retrieval", "assessment", "generation", "validation", "citations"}
)


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.states = Counter()
        self.durations = defaultdict(lambda: [0, 0.0, 0.0])

    def observe(self, stage, seconds):
        if stage not in STAGES:
            raise ValueError("unknown metric stage")
        with self.lock:
            value = self.durations[stage]
            value[0] += 1
            value[1] += seconds
            value[2] = max(value[2], seconds)

    def outcome(self, value):
        # Values originate from typed response contracts, never user text.
        state = getattr(value, "status", getattr(value, "code", "unknown"))
        with self.lock:
            self.states[state] += 1

    def snapshot(self):
        with self.lock:
            return {
                "scope": "single process; resets on restart",
                "chat_states": dict(self.states),
                "stages": {
                    k: {"count": v[0], "total_seconds": v[1], "max_seconds": v[2]}
                    for k, v in self.durations.items()
                },
            }


@contextmanager
def stage(name):
    start = time.monotonic()
    try:
        yield
    finally:
        if metrics := CURRENT_METRICS.get():
            metrics.observe(name, time.monotonic() - start)
