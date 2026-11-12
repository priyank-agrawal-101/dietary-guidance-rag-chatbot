"""Evidence decisions and model-authored claims contain no model-authored metadata."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_validator

from dietary_rag.corpus.contracts import Contract, Identifier, NonEmpty


class Support(Contract):
    chunk_id: Identifier
    support_quote: NonEmpty


class DraftClaim(Contract):
    claim_text: Annotated[str, Field(strict=True, min_length=1, max_length=12000)]
    evidence: Annotated[tuple[Support, ...], Field(min_length=1, max_length=20)]


class DraftDocument(Contract):
    document_id: Identifier
    claims: Annotated[tuple[DraftClaim, ...], Field(min_length=1, max_length=20)]


class DraftGap(Contract):
    document_id: Identifier
    aspect: Identifier
    reason: Literal["not_covered", "budget_omission"]


class DraftAnswer(Contract):
    document_answers: Annotated[tuple[DraftDocument, ...], Field(min_length=1, max_length=20)]
    unanswered_aspects: tuple[DraftGap, ...] = ()

    @model_validator(mode="after")
    def unique_documents(self):
        ids = [d.document_id for d in self.document_answers]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate draft documents")
        return self


class Aspect(Contract):
    aspect_id: Identifier
    topic: NonEmpty
    description: NonEmpty
    terms: tuple[NonEmpty, ...] = ()
    temperatures: tuple[NonEmpty, ...] = ()
    method: Literal["refrigerator", "freezer"] | None = None
    required_details: tuple[NonEmpty, ...] = ()


class EvidenceUnit(Contract):
    unit_id: Identifier
    document_id: Identifier
    chunk_id: Identifier
    claim_text: NonEmpty
    evidence: tuple[Support, ...]


class Coverage(Contract):
    document_id: Identifier
    aspect_id: Identifier
    status: Literal["supported", "needs_details", "not_covered"]
    unit_ids: tuple[Identifier, ...] = ()


class EvidenceAssessment(Contract):
    version: Literal["evidence_assessment_v1"] = "evidence_assessment_v1"
    corpus_snapshot_id: Identifier
    decision: Literal["supported", "partial", "needs_details", "not_covered"]
    aspects: tuple[Aspect, ...]
    coverage: tuple[Coverage, ...]
    units: tuple[EvidenceUnit, ...]
    required_details: tuple[NonEmpty, ...] = ()


class GenerationInput(Contract):
    question: NonEmpty
    corpus_snapshot_id: Identifier
    evidence_groups: dict[Identifier, tuple[EvidenceUnit, ...]]
    source_applicability: dict[Identifier, dict]
    instructions: NonEmpty
    output_schema: dict
    repair_codes: tuple[NonEmpty, ...] = ()
    unanswered_aspects: tuple[DraftGap, ...] = ()


class SemanticVerdict(Contract):
    supported: StrictBool
    confidence: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
    reason: Literal["entailed", "contradicted", "insufficient", "uncertain"]


class AnswerValidationError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class AnswerModelUnavailable(RuntimeError):
    pass
