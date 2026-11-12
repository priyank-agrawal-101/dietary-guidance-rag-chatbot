"""Canonical evidence, inspectable transformations, masks and exact review receipts."""

from typing import Literal

from pydantic import model_validator

from dietary_rag.corpus.contracts import (
    ContentBlock,
    Contract,
    DocumentVersion,
    Identifier,
    NonEmpty,
    NonnegativeInt,
    Sha256,
    SourceLocation,
    TableContext,
)
from dietary_rag.corpus.identity import stable_id


class NormalizedRow(Contract):
    source_row: NonnegativeInt
    physical_page: int | None = None
    category: str | None = None
    column_headers: tuple[NonEmpty, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def width(self):
        if len(self.values) != len(self.column_headers):
            raise ValueError("normalized table values must retain their header relationships")
        return self


class NormalizedTable(Contract):
    block_id: Identifier
    original_table_id: Identifier
    context: TableContext
    rows: tuple[NormalizedRow, ...]


class BlockDecision(Contract):
    original_block_id: Identifier
    original_text_sha256: Sha256
    original_locations: tuple[SourceLocation, ...]
    normalized_block_id: Identifier | None = None
    state: Literal["retained", "excluded", "quarantined"]
    rules: tuple[NonEmpty, ...]


class Transformation(Contract):
    original_block_id: Identifier
    field: NonEmpty
    rule: NonEmpty
    before: str
    after: str
    word_ids: tuple[Identifier, ...] = ()


class ExclusionEvidence(Contract):
    rule: NonEmpty
    origin: Literal["normalization", "native_extraction", "ocr_quality"]
    reason: NonEmpty
    locations: tuple[SourceLocation, ...] = ()
    subject_id: Identifier | None = None


class NormalizationReview(Contract):
    normalization_id: Identifier
    configuration_id: Identifier
    original_extraction_id: Identifier
    raw_sha256: Sha256
    block_ids: tuple[Identifier, ...]
    reviewer: NonEmpty
    scope: NonEmpty
    note: NonEmpty


class NormalizedExtraction(Contract):
    normalization_id: Identifier
    normalizer_version: NonEmpty
    configuration_id: Identifier
    original_extraction_id: Identifier
    original_artifact_sha256: Sha256
    document: DocumentVersion
    format_adapter: Literal["html", "native_pdf", "ocr"]
    comparison_only: bool = False
    fixture_kind: str | None = None
    blocks: tuple[ContentBlock, ...]
    tables: tuple[NormalizedTable, ...]
    decisions: tuple[BlockDecision, ...]
    transformations: tuple[Transformation, ...]
    exclusions: tuple[ExclusionEvidence, ...]
    review_state: Literal["pending", "reviewed", "quarantined"]
    review: NormalizationReview | None = None

    @model_validator(mode="after")
    def relationships(self):
        blocks = {b.block_id: b for b in self.blocks}
        decisions = {d.original_block_id: d for d in self.decisions}
        if len(blocks) != len(self.blocks) or len(decisions) != len(self.decisions):
            raise ValueError("normalization evidence identities must be unique")
        linked = set()
        for d in self.decisions:
            if d.state == "retained":
                if d.normalized_block_id not in blocks:
                    raise ValueError("retained decisions must resolve to canonical blocks")
                b = blocks[d.normalized_block_id]

                # Quality review changes only status; original geometry/anchors stay exact.
                def strip(loc):
                    return loc.model_dump(exclude={"review_state"})

                if [strip(loc) for loc in b.source_locations] != [
                    strip(loc) for loc in d.original_locations
                ]:
                    raise ValueError("normalization must preserve all original locations")
                linked.add(b.block_id)
                payload = b.model_dump(exclude={"block_id"})
                payload["source_locations"] = tuple(
                    loc.model_copy(update={"review_state": "pending"}) for loc in b.source_locations
                )
                if (
                    stable_id(
                        "normalized_block",
                        {
                            "config": self.configuration_id,
                            "original": d.original_block_id,
                            **payload,
                        },
                    )
                    != b.block_id
                ):
                    raise ValueError("canonical block identity differs from its normalized content")
            elif d.normalized_block_id is not None:
                raise ValueError("excluded/quarantined input cannot resolve to a retained block")
            if d.state == "quarantined" and self.review_state != "quarantined":
                raise ValueError("unresolved normalization cannot pass quality review")
        if linked != blocks.keys():
            raise ValueError("every canonical block must resolve to retained original evidence")
        if len(linked) != sum(d.state == "retained" for d in self.decisions):
            raise ValueError("normalization cannot silently deduplicate separate original blocks")
        for i, b in enumerate(self.blocks):
            if b.sequence_index != i or b.document_version_id != self.document.document_version_id:
                raise ValueError("canonical blocks must preserve ordered document-version identity")
            expected = "reviewed" if self.review_state == "reviewed" else "pending"
            if any(loc.review_state != expected for loc in b.source_locations):
                raise ValueError(
                    "canonical location review status differs from normalization review"
                )
        if {t.block_id for t in self.tables} != {
            b.block_id for b in self.blocks if b.block_type == "table"
        } or len({t.block_id for t in self.tables}) != len(self.tables):
            raise ValueError("canonical tables must resolve exactly to retained table blocks")
        if any(t.context != blocks[t.block_id].table_context for t in self.tables):
            raise ValueError("canonical table context must match its block")
        if any(t.original_block_id not in decisions for t in self.transformations):
            raise ValueError("transformations must resolve to original block decisions")
        if self.review_state == "reviewed":
            r = self.review
            if (
                r is None
                or r.normalization_id != self.normalization_id
                or r.configuration_id != self.configuration_id
                or r.original_extraction_id != self.original_extraction_id
                or r.raw_sha256 != self.document.raw_sha256
                or r.block_ids != tuple(blocks)
            ):
                raise ValueError("review receipt must cover exactly this normalized evidence")
        elif self.review is not None:
            raise ValueError("unreviewed/quarantined results cannot carry a passing review receipt")
        payload = self.model_dump(exclude={"normalization_id", "review"})
        if self.review_state == "reviewed":
            payload["review_state"] = "pending"
            payload["blocks"] = tuple(
                b.model_copy(
                    update={
                        "source_locations": tuple(
                            loc.model_copy(update={"review_state": "pending"})
                            for loc in b.source_locations
                        )
                    }
                )
                for b in self.blocks
            )
        if stable_id("normalization", payload) != self.normalization_id:
            raise ValueError("normalization identity differs from its content and audit records")
        return self
