"""Transport-neutral capacity and metrics for Streamlit's threaded sessions."""

import threading
from uuid import uuid4

from dietary_rag.api.contracts import service_error
from dietary_rag.operations.metrics import CURRENT_METRICS, Metrics


class ChatHost:
    def __init__(self, service, active, ledger, max_in_flight, *, capacity=None, metrics=None):
        self.service, self.active, self.ledger = service, active, ledger
        self.capacity = (
            capacity if capacity is not None else threading.BoundedSemaphore(max_in_flight)
        )
        self.metrics = metrics if metrics is not None else Metrics()

    def answer(self, payload):
        if not self.capacity.acquire(blocking=False):
            result = service_error(str(uuid4()), "service_busy", retry_after_seconds=2)
            self.metrics.outcome(result)
            return result
        token = CURRENT_METRICS.set(self.metrics)
        try:
            result = self.service.answer(payload)
            self.metrics.outcome(result)
            return result
        finally:
            CURRENT_METRICS.reset(token)
            self.capacity.release()
