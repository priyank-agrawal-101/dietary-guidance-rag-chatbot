"""One shared gated orchestration path: assess, draft, validate, cite, then display."""

import time
from dataclasses import dataclass, field

from dietary_rag.answering.adapters import (
    ExtractiveGenerator,
    ExtractiveSemanticVerifier,
    generation_input,
)
from dietary_rag.answering.citations import render_response
from dietary_rag.answering.contracts import AnswerModelUnavailable, AnswerValidationError, DraftGap
from dietary_rag.answering.evidence import EvidenceAssessor
from dietary_rag.api.context import CURRENT_REQUEST, request_context
from dietary_rag.api.contracts import ChatResponse, ServiceError, service_error
from dietary_rag.operations.metrics import stage
from dietary_rag.policy.runtime import PreparedRequest
from dietary_rag.policy.service import ClassificationUnavailable, PolicyViolation
from dietary_rag.providers.usage import TokenBudgetExceeded, UsageLimitExceeded
from dietary_rag.retrieval.context import food_group_context
from dietary_rag.settings import AnsweringSettings


@dataclass(frozen=True)
class AnswerInspection:
    response: ChatResponse | ServiceError
    audit: dict = field(default_factory=dict)

    def model_dump(self):
        return {"response": self.response.model_dump(mode="json"), "inspection": self.audit}


class AnswerService:
    def __init__(self, runtime, *, settings=None, generator=None, verifier=None):
        self.runtime = runtime
        self.settings = settings or AnsweringSettings()
        self.generator = generator or (
            ExtractiveGenerator() if self.settings.generator == "extractive_v1" else None
        )
        self.verifier = verifier or ExtractiveSemanticVerifier()
        self.assessor = EvidenceAssessor(runtime.policy, self.settings)

    def answer(self, payload):
        return self.inspect(payload).response

    def inspect(self, payload):
        with request_context(self.runtime.runtime.request_timeout_seconds):
            return self._inspect(payload)

    def _inspect(self, payload):
        started = time.monotonic()
        deadline = CURRENT_REQUEST.get().deadline
        audit = {
            "version": "answer_inspection_v1",
            "generation_attempts": 0,
            "validation_codes": [],
        }
        with self.runtime.prepare(payload) as prepared:
            if not isinstance(prepared, PreparedRequest):
                return AnswerInspection(prepared, audit)
            request_id = prepared.request_id
            try:
                with stage("retrieval"):
                    prepared = food_group_context(prepared, self.runtime.runtime.candidate_chunks)
                audit["search_audit"] = prepared.search_result.audit.model_dump(mode="json")
                with stage("assessment"):
                    assessment = self.assessor.assess(prepared.search_result)
                audit["assessment"] = assessment.model_dump(mode="json")
                self._deadline(deadline)
                searched = prepared.search_result.audit.searched_document_ids
                docs = {
                    h.document.document_id: h.document.document_name
                    for h in prepared.search_result.hits
                }
                scope_label = ", ".join(f"{docs.get(d, d)} ({d})" for d in searched)
                if assessment.decision == "needs_details":
                    response = ChatResponse(
                        request_id=request_id,
                        status="needs_clarification",
                        corpus_version=assessment.corpus_snapshot_id,
                        searched_documents=searched,
                        message="Please specify " + ", ".join(assessment.required_details) + ".",
                    )
                    return AnswerInspection(response, audit)
                if assessment.decision == "not_covered":
                    response = ChatResponse(
                        request_id=request_id,
                        status="not_covered",
                        corpus_version=assessment.corpus_snapshot_id,
                        searched_documents=searched,
                        message="I couldn't find a supported answer in the sources searched. Try a more specific food question or choose different sources.",
                    )
                    return AnswerInspection(response, audit)
                if self.generator is None:
                    raise AnswerModelUnavailable("generation is unconfigured")
                generation = generation_input(
                    prepared, assessment, self.settings.max_claims_per_document
                )
                # Budget omissions remain explicit coverage gaps, not implicit completeness.
                generated_ids = {
                    u.unit_id for group in generation.evidence_groups.values() for u in group
                }
                aspects = {a.aspect_id: a for a in assessment.aspects}
                unanswered = []
                gaps = []
                for coverage in assessment.coverage:
                    if prepared.request.scope.mode == "all" and coverage.status != "supported":
                        # A source that has no answer is not an unanswered part
                        # when another searched source covers the same aspect.
                        if any(
                            other.aspect_id == coverage.aspect_id
                            and other.status == "supported"
                            and set(other.unit_ids) <= generated_ids
                            for other in assessment.coverage
                        ):
                            continue
                    if (
                        coverage.status != "supported"
                        or not set(coverage.unit_ids) <= generated_ids
                    ):
                        reason = (
                            "no sufficient retrieved evidence"
                            if coverage.status != "supported"
                            else "answer budget omitted the supported evidence"
                        )
                        unanswered.append(
                            f"{docs.get(coverage.document_id, coverage.document_id)} ({coverage.document_id}): {aspects[coverage.aspect_id].description}; {reason}. Searched: {scope_label}."
                        )
                        gaps.append(
                            DraftGap(
                                document_id=coverage.document_id,
                                aspect=coverage.aspect_id,
                                reason="not_covered"
                                if coverage.status != "supported"
                                else "budget_omission",
                            )
                        )
                generation = generation.model_copy(update={"unanswered_aspects": tuple(gaps)})
                for attempt in range(self.settings.repair_attempts + 1):
                    self._deadline(deadline)
                    audit["generation_attempts"] += 1
                    try:
                        try:
                            from dietary_rag.answering.contracts import GenerationInput

                            provider_input = GenerationInput.model_validate(
                                generation.model_dump(mode="python")
                            )
                            stage_started = time.monotonic()
                            with stage("generation"):
                                draft = self.generator.generate(provider_input)
                            if (
                                time.monotonic() - stage_started
                                > self.settings.generator_timeout_seconds
                            ):
                                raise TimeoutError("generation deadline exceeded")
                        except (TimeoutError, AnswerModelUnavailable, AnswerValidationError):
                            raise
                        except Exception as exc:
                            raise AnswerModelUnavailable(
                                "generation dependency unavailable"
                            ) from exc
                        self._deadline(deadline)
                        with stage("validation"):
                            _draft, validated = validate_with_deadline(
                                draft,
                                prepared,
                                generation,
                                self.verifier,
                                self.runtime.policy,
                                self.settings.semantic_minimum_confidence,
                                deadline,
                                self.settings.verifier_timeout_seconds,
                            )
                        with stage("citations"):
                            response = render_response(prepared, assessment, validated, unanswered)
                        self.runtime.check_response_policy(response)
                        self._deadline(deadline)
                        audit["validated_draft"] = _draft.model_dump(mode="json")
                        provenance = {}
                        for _doc, claim, _units in validated:
                            for support in claim.evidence:
                                if support.chunk_id in provenance:
                                    continue
                                resolved = prepared.evidence_request.resolve_evidence(
                                    support.chunk_id
                                )
                                chunk, document = resolved["chunk"], resolved["build"].document
                                provenance[support.chunk_id] = {
                                    "document_id": document.document_id,
                                    "document_version_id": document.document_version_id,
                                    "raw_sha256": document.raw_sha256,
                                    "text_sha256": chunk.text_sha256,
                                    "source_locations": [
                                        loc.model_dump(mode="json")
                                        for loc in chunk.source_locations
                                    ],
                                    "spans": [span.model_dump(mode="json") for span in chunk.spans],
                                    "table_rows": chunk.table_rows.model_dump(mode="json")
                                    if chunk.table_rows
                                    else None,
                                }
                        audit["claim_evidence"] = provenance
                        audit["elapsed_seconds"] = time.monotonic() - started
                        return AnswerInspection(response, audit)
                    except (AnswerValidationError, PolicyViolation) as exc:
                        code = (
                            exc.code
                            if isinstance(exc, AnswerValidationError)
                            else "post_generation_policy"
                        )
                        audit["validation_codes"].append(code)
                        if attempt == self.settings.repair_attempts:
                            return AnswerInspection(
                                service_error(request_id, "answer_validation_failed"), audit
                            )
                        # No new retrieval, evidence, previous raw draft or source instructions.
                        generation = generation.model_copy(update={"repair_codes": (code,)})
            except TimeoutError:
                response = service_error(request_id, "request_timeout")
            except UsageLimitExceeded as exc:
                response = service_error(
                    request_id, "usage_limit_exceeded", retry_after_seconds=exc.retry_after_seconds
                )
            except TokenBudgetExceeded:
                response = service_error(request_id, "token_budget_exceeded")
            except (AnswerModelUnavailable, ClassificationUnavailable):
                response = service_error(request_id, "model_unavailable")
            except (OSError, KeyError, ValueError, RuntimeError):
                response = service_error(request_id, "corpus_unavailable")
            return AnswerInspection(response, audit)

    @staticmethod
    def _deadline(deadline):
        if time.monotonic() > deadline:
            raise TimeoutError("answer deadline exceeded")


def validate_with_deadline(
    draft, prepared, generation, verifier, policy, confidence, deadline, verifier_timeout
):
    from dietary_rag.answering.validation import validate_draft

    class DeadlineVerifier:
        def verify(self, claim, units):
            AnswerService._deadline(deadline)
            try:
                stage_started = time.monotonic()
                result = verifier.verify(claim, units)
                if time.monotonic() - stage_started > verifier_timeout:
                    raise TimeoutError("semantic verification deadline exceeded")
            except (TimeoutError, AnswerModelUnavailable, AnswerValidationError):
                raise
            except Exception as exc:
                raise AnswerModelUnavailable("semantic verification unavailable") from exc
            AnswerService._deadline(deadline)
            return result

    return validate_draft(draft, prepared, generation, DeadlineVerifier(), policy, confidence)
