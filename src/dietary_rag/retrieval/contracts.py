"""Versioned retrieval parameters, requests, ranked evidence and actual search audits."""

from typing import Annotated, Literal

from pydantic import Field, FiniteFloat, StrictBool, model_validator

from dietary_rag.corpus.contracts import (
    Contract,
    DocumentVersion,
    Identifier,
    NonEmpty,
    PositiveInt,
    SourceTier,
)
from dietary_rag.corpus.identity import stable_id
from dietary_rag.ingestion.chunk_contracts import Chunk

Strategy = Literal["dense", "bm25", "hybrid"]


class QueryModelUnavailable(RuntimeError):
    """The query embedding dependency failed, separately from corpus integrity."""


class InvalidSearchRequest(ValueError):
    """Resolved selection or input exceeds configured request limits."""


class RetrievalConfig(Contract):
    version: Literal["retrieval_v1"] = "retrieval_v1"
    strategy: Strategy
    resolution_configuration_id: Identifier
    branch_candidates: Annotated[int, Field(strict=True, ge=5, le=100)] = 20
    candidate_chunks: Annotated[int, Field(strict=True, ge=5, le=100)] = 20
    per_document_candidates: Annotated[int, Field(strict=True, ge=1, le=20)] = 3
    rrf_constant: PositiveInt = 60
    bm25_k1: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 1.2
    bm25_b: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] = 0.75
    lexical_preprocessing: Literal["ascii_alnum_casefold_v1"] = "ascii_alnum_casefold_v1"
    deduplication: Literal["same_document_span_containment_v1"] = (
        "same_document_span_containment_v1"
    )
    scope_policy: Literal["reviewed_us_consumer_v1"] = "reviewed_us_consumer_v1"

    @property
    def configuration_id(self):
        return stable_id("retrieval_config", self.model_dump())

    @model_validator(mode="after")
    def budgets(self):
        if self.per_document_candidates > self.branch_candidates:
            raise ValueError("per-document candidates exceed branch budget")
        return self


class SearchRequest(Contract):
    question: NonEmpty
    selected_document_ids: tuple[Identifier, ...] | None = None
    document_names: tuple[NonEmpty, ...] | None = None
    source_tiers: tuple[SourceTier, ...] | None = None
    official_only: StrictBool = False
    comparison: StrictBool = False
    limit: PositiveInt = 5

    @model_validator(mode="after")
    def selections(self):
        if any(
            value == ()
            for value in (self.selected_document_ids, self.document_names, self.source_tiers)
        ):
            raise ValueError("empty selections cannot become full-corpus search")
        if self.selected_document_ids and len(set(self.selected_document_ids)) != len(
            self.selected_document_ids
        ):
            raise ValueError("duplicate selected document IDs")
        if (
            self.official_only
            and self.source_tiers
            and "official_guidance" not in self.source_tiers
        ):
            raise ValueError("official-only selection conflicts with source tiers")
        return self


class ScopeIssue(Contract):
    kind: Literal["unknown", "ambiguous", "unavailable", "conflicting", "invalid_name_syntax"]
    selection: NonEmpty
    candidate_document_ids: tuple[Identifier, ...] = ()
    reason: NonEmpty


class SearchAudit(Contract):
    release_id: Identifier | None = None
    activation_generation: PositiveInt | None = None
    status: Literal["searched", "invalid_selection", "no_eligible_documents"]
    corpus_snapshot_id: Identifier
    retrieval_configuration_id: Identifier
    strategy: Strategy
    resolved_question: NonEmpty
    selected_document_ids: tuple[Identifier, ...] | None
    searched_document_ids: tuple[Identifier, ...] = ()
    scope_issues: tuple[ScopeIssue, ...] = ()
    filtered_documents: dict[str, str] = Field(default_factory=dict)
    branch_ranks: dict[str, list[dict]] = Field(default_factory=dict)
    fused_ranks: list[dict] = Field(default_factory=list)
    budget_omissions: dict[str, str] = Field(default_factory=dict)
    duplicate_omissions: dict[str, str] = Field(default_factory=dict)
    context_chunks: dict[str, str] = Field(default_factory=dict)
    query_embedding_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0
    elapsed_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0

    @model_validator(mode="after")
    def pinned_identity(self):
        if (self.release_id is None) != (self.activation_generation is None):
            raise ValueError("active search audits require release and generation together")
        return self


class EvidenceHit(Contract):
    chunk: Chunk
    document: DocumentVersion
    rank: PositiveInt
    score: FiniteFloat
    branch_ranks: dict[str, int]


class SearchResult(Contract):
    hits: tuple[EvidenceHit, ...]
    audit: SearchAudit

    @model_validator(mode="after")
    def validate_scope(self):
        if self.audit.status != "searched" and (self.hits or self.audit.searched_document_ids):
            raise ValueError("unsuccessful scope resolution cannot claim a search")
        for rank, hit in enumerate(self.hits, 1):
            if (
                hit.rank != rank
                or hit.chunk.document_id != hit.document.document_id
                or hit.chunk.document_version_id != hit.document.document_version_id
                or hit.document.document_id not in self.audit.searched_document_ids
            ):
                raise ValueError("retrieved evidence differs from searched scope")
        return self
