"""Policy-before-I/O entry point, retaining one pinned request for Phase 5 answering."""

import time
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4

from pydantic import ValidationError

from dietary_rag.api.context import CURRENT_REQUEST
from dietary_rag.api.contracts import ChatRequest, ChatResponse, service_error
from dietary_rag.operations.metrics import stage
from dietary_rag.policy.service import ClassificationUnavailable, PolicyService
from dietary_rag.policy.templates import MESSAGES
from dietary_rag.retrieval.contracts import (
    InvalidSearchRequest,
    QueryModelUnavailable,
    SearchResult,
)
from dietary_rag.settings import RuntimeSettings


@dataclass(frozen=True)
class PreparedRequest:
    request_id: str
    request: ChatRequest
    search_result: SearchResult
    evidence_request: object


class PolicyRuntime:
    """open_request is a lazy factory returning a compatible pinned context manager.

    Policy rejection never opens corpus/model resources. Phase 5 consumes a
    PreparedRequest inside this context, validates evidence/claims, then renders.
    """

    def __init__(self, open_request, *, runtime=None, policy=None):
        self.open_request = open_request
        self.runtime = runtime or RuntimeSettings()
        self.policy = policy or PolicyService()

    @classmethod
    def for_active(
        cls, store, registry, tokenizer, processor, resolver, runtime, *, policy=None, **kwargs
    ):
        def open_request():
            return store.pin().open_request(
                registry, tokenizer, processor, resolver, runtime, **kwargs
            )

        return cls(open_request, runtime=runtime, policy=policy)

    @contextmanager
    def prepare(self, payload):
        context = CURRENT_REQUEST.get()
        request_id = context.request_id if context else str(uuid4())
        started = time.monotonic()
        deadline = context.deadline if context else started + self.runtime.request_timeout_seconds
        try:
            # Round-trip existing instances too; model_copy/model_construct cannot bypass limits.
            if isinstance(payload, ChatRequest):
                payload = payload.model_dump(mode="python")
            request = ChatRequest.model_validate(payload, context={"runtime": self.runtime})
        except (ValidationError, ValueError, TypeError):
            yield service_error(request_id, "invalid_request")
            return
        try:
            if time.monotonic() >= deadline:
                raise TimeoutError("request deadline exceeded")
            with stage("policy"):
                decision = self.policy.decide(request.question)
        except TimeoutError:
            yield service_error(request_id, "request_timeout")
            return
        except ClassificationUnavailable:
            yield service_error(request_id, "model_unavailable")
            return
        if time.monotonic() > deadline:
            yield service_error(request_id, "request_timeout")
            return
        if decision.route != "allow":
            yield ChatResponse(
                request_id=request_id, status=decision.route, message=MESSAGES[decision.template]
            )
            return

        # Keep exception translation outside yield: caller/answering errors must
        # not be swallowed or cause a contextmanager to yield a second time.
        from contextlib import ExitStack

        with ExitStack() as stack:
            try:
                with stage("corpus_open"):
                    evidence_request = stack.enter_context(self.open_request())
                if time.monotonic() > deadline:
                    raise TimeoutError("request deadline exceeded")
                with stage("retrieval"):
                    result = evidence_request.search(request.search_request())
                if time.monotonic() > deadline:
                    raise TimeoutError("request deadline exceeded")
            except TimeoutError:
                outcome = service_error(request_id, "request_timeout")
            except QueryModelUnavailable:
                outcome = service_error(request_id, "model_unavailable")
            except InvalidSearchRequest:
                outcome = service_error(request_id, "invalid_request")
            except Exception:
                outcome = service_error(request_id, "corpus_unavailable")
            else:
                issues = result.audit.scope_issues
                if result.audit.status == "invalid_selection":
                    if any(i.kind == "unknown" for i in issues):
                        outcome = service_error(
                            request_id,
                            "unknown_document",
                            tuple(
                                i.selection
                                for i in issues
                                if i.kind == "unknown"
                                and i.selection
                                in (request.search_request().selected_document_ids or ())
                            ),
                            scope_issues=issues,
                        )
                    elif any(i.kind == "unavailable" for i in issues):
                        outcome = service_error(
                            request_id,
                            "source_unavailable",
                            tuple(
                                dict.fromkeys(d for i in issues for d in i.candidate_document_ids)
                            ),
                            scope_issues=issues,
                        )
                    else:
                        outcome = ChatResponse(
                            request_id=request_id,
                            status="needs_clarification",
                            message=MESSAGES["selection"],
                        )
                elif result.audit.status == "no_eligible_documents":
                    outcome = service_error(request_id, "corpus_unavailable")
                else:
                    # Empty/relevant hits are NOT an evidence-sufficiency decision.
                    outcome = PreparedRequest(request_id, request, result, evidence_request)
            yield outcome

    def check_response_policy(self, response):
        """Mandatory final policy hook for Phase 5; grounding is a separate validator."""
        if isinstance(response, ChatResponse):
            response = response.model_dump(mode="python")
        response = ChatResponse.model_validate(response)
        if response.status == "answered":
            self.policy.check_claims(
                claim.text for answer in response.document_answers for claim in answer.claims
            )
        return response
