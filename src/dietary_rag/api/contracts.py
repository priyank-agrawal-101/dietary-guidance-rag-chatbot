"""Transport-neutral API contracts; HTTP handlers are introduced in Phase 6."""

from typing import Annotated, Literal

from pydantic import AnyHttpUrl, Field, StrictBool, ValidationInfo, field_validator, model_validator

from dietary_rag.corpus.contracts import (
    Contract,
    Identifier,
    NonEmpty,
    PositiveInt,
    SourceFormat,
    SourceTier,
)
from dietary_rag.retrieval.contracts import ScopeIssue, SearchRequest
from dietary_rag.settings import RuntimeSettings


class AllScope(Contract):
    mode: Literal["all"] = "all"


class SelectedScope(Contract):
    mode: Literal["selected"]
    document_ids: Annotated[tuple[Identifier, ...], Field(min_length=1)]

    @field_validator("document_ids")
    @classmethod
    def deduplicate(cls, value):
        return tuple(dict.fromkeys(value))


class ChatRequest(Contract):
    question: Annotated[str, Field(strict=True, min_length=1)]
    scope: Annotated[AllScope | SelectedScope, Field(discriminator="mode")] = Field(
        default_factory=AllScope
    )
    source_tiers: Annotated[tuple[SourceTier, ...], Field(min_length=1, max_length=4)] | None = None
    official_only: StrictBool = False
    limit: Annotated[int, Field(strict=True, ge=1)] = 5

    @field_validator("question")
    @classmethod
    def clean_question(cls, value, info: ValidationInfo):
        runtime = (info.context or {}).get("runtime", RuntimeSettings())
        if len(value) > runtime.question_max_characters:
            raise ValueError("question exceeds configured limit")
        if not value.strip():
            raise ValueError("question must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def runtime_limits(self, info: ValidationInfo):
        runtime = (info.context or {}).get("runtime", RuntimeSettings())
        if runtime:
            if len(self.question) > runtime.question_max_characters:
                raise ValueError("question exceeds configured limit")
            if (
                isinstance(self.scope, SelectedScope)
                and len(self.scope.document_ids) > runtime.selected_document_limit
            ):
                raise ValueError("selection exceeds configured limit")
            if self.limit > runtime.candidate_chunks:
                raise ValueError("candidate limit exceeds configured limit")
        self.search_request()  # Reject contradictory filters before policy or I/O.
        return self

    def search_request(self):
        selected = self.scope.document_ids if isinstance(self.scope, SelectedScope) else None
        return SearchRequest(
            question=self.question,
            selected_document_ids=selected,
            source_tiers=self.source_tiers,
            official_only=self.official_only,
            comparison=selected is not None and len(selected) > 1,
            limit=self.limit,
        )


class Claim(Contract):
    text: NonEmpty
    citation_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]


class DocumentAnswer(Contract):
    document_id: Identifier
    claims: Annotated[tuple[Claim, ...], Field(min_length=1)]


class Citation(Contract):
    citation_id: NonEmpty
    document_id: Identifier
    document_name: NonEmpty
    publisher: NonEmpty
    source_tier: SourceTier
    jurisdiction: NonEmpty | None = None
    publication_year: Annotated[int, Field(strict=True, ge=1900, le=2100)] | None = None
    date_label: NonEmpty | None = None
    format: SourceFormat
    physical_pages: tuple[PositiveInt, ...] = ()
    page_urls: tuple[AnyHttpUrl, ...] = ()
    printed_page_labels: tuple[NonEmpty, ...] = ()
    source_url: AnyHttpUrl
    section_heading: NonEmpty
    chunk_ids: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    support_quotes: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def locations(self):
        if self.format == "pdf" and not self.physical_pages:
            raise ValueError("PDF citations require physical pages")
        if self.format == "html" and (self.physical_pages or self.printed_page_labels):
            raise ValueError("HTML citations cannot invent PDF locations")
        if self.page_urls and (
            self.format != "pdf" or len(self.page_urls) != len(self.physical_pages)
        ):
            raise ValueError("page links must correspond to PDF physical pages")
        return self


class ChatResponse(Contract):
    request_id: NonEmpty
    status: Literal[
        "answered", "not_covered", "out_of_scope", "requires_structured_data", "needs_clarification"
    ]
    corpus_version: Identifier | None = None
    message: NonEmpty | None = None
    document_answers: tuple[DocumentAnswer, ...] = ()
    citations: tuple[Citation, ...] = ()
    searched_documents: tuple[Identifier, ...] = ()
    unanswered_aspects: tuple[NonEmpty, ...] = ()

    @model_validator(mode="after")
    def state_invariants(self):
        if len(set(self.searched_documents)) != len(self.searched_documents):
            raise ValueError("duplicate searched documents")
        if self.searched_documents and self.corpus_version is None:
            raise ValueError("searched scope requires corpus version")
        if self.status == "answered":
            if not self.document_answers or not self.citations or not self.searched_documents:
                raise ValueError("answered requires claims, citations and actual search scope")
            citations = {c.citation_id: c for c in self.citations}
            if len(citations) != len(self.citations):
                raise ValueError("duplicate citation IDs")
            used, documents = set(), set()
            for answer in self.document_answers:
                if (
                    answer.document_id in documents
                    or answer.document_id not in self.searched_documents
                ):
                    raise ValueError("answer document must be unique and searched")
                documents.add(answer.document_id)
                for claim in answer.claims:
                    for identifier in claim.citation_ids:
                        if (
                            identifier not in citations
                            or citations[identifier].document_id != answer.document_id
                        ):
                            raise ValueError("claim citation must belong to its document")
                        used.add(identifier)
            if used != set(citations):
                raise ValueError("unused citations")
        else:
            if not self.message or self.document_answers or self.citations:
                raise ValueError("non-answer requires message and cannot contain claims/citations")
            if (
                self.status in ("out_of_scope", "requires_structured_data")
                and self.searched_documents
            ):
                raise ValueError("pre-retrieval decisions cannot claim searched documents")
            if self.status == "not_covered" and not self.searched_documents:
                raise ValueError("evidence gap requires actual searched scope")
        return self


ErrorCode = Literal[
    "invalid_request",
    "unknown_document",
    "source_unavailable",
    "corpus_unavailable",
    "model_unavailable",
    "answer_validation_failed",
    "request_timeout",
    "usage_limit_exceeded",
    "token_budget_exceeded",
    "service_busy",
]
ERROR_HTTP_STATUS = {
    "invalid_request": 422,
    "unknown_document": 404,
    "source_unavailable": 409,
    "corpus_unavailable": 503,
    "model_unavailable": 503,
    "answer_validation_failed": 502,
    "request_timeout": 504,
    "usage_limit_exceeded": 429,
    "token_budget_exceeded": 422,
    "service_busy": 503,
}
ERROR_MESSAGES = {
    "invalid_request": "The request has invalid fields, scope, or limits.",
    "unknown_document": "A selected document is not recognized.",
    "source_unavailable": "A selected source is unavailable in the active corpus or requested filters.",
    "corpus_unavailable": "The active corpus is absent, incompatible, or unavailable.",
    "model_unavailable": "The intent or generation dependency is unavailable.",
    "answer_validation_failed": "The generated answer failed validation and has been withheld.",
    "request_timeout": "The request could not complete within its deadline.",
    "usage_limit_exceeded": "The generation usage limit has been reached. Try again later.",
    "token_budget_exceeded": "The complete evidence exceeds the generation token budget. Select fewer documents or narrow the question.",
    "service_busy": "The service is handling its maximum number of requests. Try again later.",
}


class ServiceError(Contract):
    request_id: NonEmpty
    code: ErrorCode
    message: NonEmpty
    document_ids: tuple[Identifier, ...] = ()
    scope_issues: tuple[ScopeIssue, ...] = ()
    retry_after_seconds: PositiveInt | None = None

    @property
    def http_status(self):
        return ERROR_HTTP_STATUS[self.code]


def service_error(request_id, code, document_ids=(), scope_issues=(), *, retry_after_seconds=None):
    return ServiceError(
        request_id=request_id,
        code=code,
        message=ERROR_MESSAGES[code],
        document_ids=document_ids,
        scope_issues=scope_issues,
        retry_after_seconds=retry_after_seconds,
    )


class LivenessResponse(Contract):
    status: Literal["alive"] = "alive"


class ReadinessResponse(Contract):
    status: Literal["ready"] = "ready"
    corpus_version: Identifier
    release_id: Identifier
    activation_generation: PositiveInt
    available_documents: Annotated[int, Field(strict=True, ge=0)]
    generation: NonEmpty


class DocumentAvailability(Contract):
    document_id: Identifier
    document_name: NonEmpty
    publisher: NonEmpty
    source_url: AnyHttpUrl
    format: SourceFormat
    source_tier: SourceTier
    jurisdiction: NonEmpty
    target_audience: NonEmpty
    content_role: NonEmpty
    dates: dict
    available: StrictBool
    availability_reason: Literal["active", "corpus_unavailable", "not_in_active_corpus"]


class DocumentListResponse(Contract):
    request_id: NonEmpty
    corpus_version: Identifier | None
    release_id: Identifier | None
    documents: tuple[DocumentAvailability, ...]
    error: ServiceError | None = None
