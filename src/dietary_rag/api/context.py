"""Request identity and absolute monotonic deadline, propagated to worker threads."""

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    deadline: float


CURRENT_REQUEST = ContextVar("dietary_request", default=None)


@contextmanager
def request_context(timeout_seconds, *, request_id=None):
    current = CURRENT_REQUEST.get()
    if current is not None:
        yield current
        return
    context = RequestContext(request_id or str(uuid4()), time.monotonic() + timeout_seconds)
    token = CURRENT_REQUEST.set(context)
    try:
        yield context
    finally:
        CURRENT_REQUEST.reset(token)


def remaining_seconds(stage_seconds):
    current = CURRENT_REQUEST.get()
    remaining = stage_seconds
    if current is not None:
        remaining = min(remaining, current.deadline - time.monotonic())
    if remaining <= 0:
        raise TimeoutError("request deadline exceeded")
    return remaining
