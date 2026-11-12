"""Inspectable native HTML evidence, before normalization and chunking."""

from typing import Literal

from pydantic import model_validator

from dietary_rag.corpus.contracts import (
    ContentBlock,
    Contract,
    DocumentVersion,
    Identifier,
    NonEmpty,
    NonnegativeInt,
    PositiveInt,
)


class HTMLDiagnostic(Contract):
    code: NonEmpty
    dom_path: NonEmpty | None = None
    detail: NonEmpty


class HTMLCell(Contract):
    row: NonnegativeInt
    column: NonnegativeInt
    rowspan: PositiveInt
    colspan: PositiveInt
    is_header: bool
    scope: str | None = None
    text: str  # Empty source cells stay empty, never guessed.
    dom_path: NonEmpty


class HTMLTableRow(Contract):
    source_row: NonnegativeInt
    category: str | None = None
    column_headers: tuple[NonEmpty, ...]
    values: tuple[str, ...]


class HTMLTable(Contract):
    block_id: Identifier
    original_html: NonEmpty  # Parsed DOM serialization; exact bytes remain in the artifact store.
    cells: tuple[HTMLCell, ...]
    grid: tuple[tuple[NonnegativeInt, ...], ...]  # Origin cell indexes, expanded spans.
    rows: tuple[HTMLTableRow, ...]

    @model_validator(mode="after")
    def validate_grid(self):
        if not self.cells or not self.grid or not self.grid[0] or not self.rows:
            raise ValueError("native tables require cells, a grid and logical data rows")
        width = len(self.grid[0])
        if any(len(row) != width for row in self.grid):
            raise ValueError("native table grid must be rectangular")
        for r, row in enumerate(self.grid):
            for c, index in enumerate(row):
                if index >= len(self.cells):
                    raise ValueError("grid index does not resolve to a source cell")
                cell = self.cells[index]
                if not (
                    cell.row <= r < cell.row + cell.rowspan
                    and cell.column <= c < cell.column + cell.colspan
                ):
                    raise ValueError("grid position differs from its original cell span")
        if any(
            len(row.values) != width
            or len(row.column_headers) != width
            or row.source_row >= len(self.grid)
            for row in self.rows
        ):
            raise ValueError("logical rows must preserve the grid width and source row")
        return self


class HTMLExtraction(Contract):
    extraction_id: Identifier
    parser_version: NonEmpty
    profile: NonEmpty
    configuration_id: Identifier
    document: DocumentVersion
    blocks: tuple[ContentBlock, ...]
    tables: tuple[HTMLTable, ...]
    diagnostics: tuple[HTMLDiagnostic, ...]
    review_state: Literal["pending"] = "pending"

    @model_validator(mode="after")
    def validate_relationships(self):
        if any(
            block.document_version_id != self.document.document_version_id
            or block.sequence_index != index
            for index, block in enumerate(self.blocks)
        ):
            raise ValueError("blocks must be ordered and resolve to this document version")
        block_ids = {b.block_id for b in self.blocks}
        table_ids = {t.block_id for t in self.tables}
        if len(block_ids) != len(self.blocks) or len(table_ids) != len(self.tables):
            raise ValueError("native evidence identities must be unique")
        if table_ids != {b.block_id for b in self.blocks if b.block_type == "table"}:
            raise ValueError("each native table must resolve to exactly one table block")
        return self
