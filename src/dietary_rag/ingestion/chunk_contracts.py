"""Chunk text, exact block slices, table rows and immutable build provenance."""

from typing import Literal

from pydantic import model_validator

from dietary_rag.corpus.contracts import (
    Contract,
    DocumentVersion,
    Identifier,
    NonEmpty,
    NonnegativeInt,
    PositiveInt,
    SourceLocation,
    TableContext,
)
from dietary_rag.corpus.identity import sha256_bytes, stable_id


class ChunkSpan(Contract):
    block_id: Identifier
    start: NonnegativeInt
    end: PositiveInt
    overlap: bool = False

    @model_validator(mode="after")
    def valid_range(self):
        if self.end <= self.start:
            raise ValueError("chunk spans must have positive original-text length")
        return self


class ChunkRows(Contract):
    block_id: Identifier
    original_table_id: Identifier
    row_indices: tuple[NonnegativeInt, ...]


class Chunk(Contract):
    chunk_id: Identifier
    document_id: Identifier
    document_version_id: Identifier
    family_id: Identifier
    sequence_index: NonnegativeInt
    chunk_type: Literal["prose", "list", "table"]
    heading_path: tuple[NonEmpty, ...]
    region_keys: tuple[NonEmpty, ...]
    text: NonEmpty
    embedding_text: NonEmpty
    token_count: PositiveInt
    text_sha256: NonEmpty
    spans: tuple[ChunkSpan, ...] = ()
    table_rows: ChunkRows | None = None
    table_context: TableContext | None = None
    source_locations: tuple[SourceLocation, ...]

    @model_validator(mode="after")
    def relationships(self):
        if self.text_sha256 != sha256_bytes(self.text.encode()):
            raise ValueError("chunk text hash mismatch")
        if not self.embedding_text.startswith("passage: ") or not self.embedding_text.endswith(
            self.text
        ):
            raise ValueError(
                "embedding text must contain the exact evidence body and E5 passage prefix"
            )
        if not self.source_locations or any(
            loc.review_state != "reviewed" for loc in self.source_locations
        ):
            raise ValueError("chunks require reviewed original locations")
        if self.chunk_type == "table":
            if (
                self.spans
                or self.table_rows is None
                or not self.table_rows.row_indices
                or self.table_context is None
            ):
                raise ValueError("table chunks require original rows/context, not prose slices")
        elif not self.spans or self.table_rows is not None or self.table_context is not None:
            raise ValueError("prose/list chunks require original block slices")
        return self


class ChunkBuild(Contract):
    build_id: Identifier
    chunker_version: NonEmpty
    configuration_id: Identifier
    normalization_id: Identifier
    original_extraction_id: Identifier
    document: DocumentVersion
    role: Literal["consumer", "comparison", "fixture"]
    tokenizer: dict
    target_tokens: PositiveInt
    overlap_tokens: NonnegativeInt
    input_limit: PositiveInt
    chunks: tuple[Chunk, ...]
    diagnostics: tuple[NonEmpty, ...]
    state: Literal["ready", "quarantined"]

    @model_validator(mode="after")
    def identities(self):
        if (self.state == "ready") != (not self.diagnostics):
            raise ValueError("chunk diagnostics must quarantine the entire build")
        if self.target_tokens > self.input_limit or self.overlap_tokens >= self.target_tokens:
            raise ValueError("chunk budgets must fit verified model limit")
        if len({c.chunk_id for c in self.chunks}) != len(self.chunks):
            raise ValueError("chunk identities must be unique")
        for index, chunk in enumerate(self.chunks):
            if (
                chunk.sequence_index != index
                or chunk.document_version_id != self.document.document_version_id
                or chunk.document_id != self.document.document_id
                or chunk.family_id != self.document.family_id
                or chunk.token_count > self.input_limit
            ):
                raise ValueError("chunk order/provenance/token limit mismatch")
            payload = chunk.model_dump(exclude={"chunk_id"})
            if (
                stable_id(
                    "chunk",
                    {
                        "config": self.configuration_id,
                        "normalization": self.normalization_id,
                        **payload,
                    },
                )
                != chunk.chunk_id
            ):
                raise ValueError("chunk identity differs from evidence")
        if stable_id("chunk_build", self.model_dump(exclude={"build_id"})) != self.build_id:
            raise ValueError("chunk build identity mismatch")
        return self
