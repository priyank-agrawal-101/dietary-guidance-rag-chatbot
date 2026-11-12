"""Native PDF geometry and review artifacts, independent of OCR/chunking."""

from typing import Literal

from pydantic import FiniteFloat, model_validator

from dietary_rag.corpus.contracts import (
    ContentBlock,
    Contract,
    DocumentVersion,
    Identifier,
    NonEmpty,
    NonnegativeInt,
    PositiveInt,
    SourceLocation,
)


class PDFWord(Contract):
    word_id: Identifier
    text: NonEmpty
    location: SourceLocation
    font_name: NonEmpty
    font_size: FiniteFloat


class PDFDiagnostic(Contract):
    code: NonEmpty
    detail: NonEmpty
    physical_page: PositiveInt | None = None
    bbox: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat] | None = None


class PDFPage(Contract):
    physical_page: PositiveInt
    printed_page_label: NonEmpty | None = None
    pdf_page_label: NonEmpty | None = None
    label_origin: Literal["pdf_page_labels", "visible_footer", "unknown"]
    width: FiniteFloat
    height: FiniteFloat
    rotation: int
    native_word_count: NonnegativeInt
    image_count: NonnegativeInt
    omitted_word_ids: tuple[Identifier, ...]
    status: Literal["native", "no_native_text", "excluded"]


class PDFBlockLayout(Contract):
    block_id: Identifier
    region_key: NonEmpty
    word_ids: tuple[Identifier, ...]


class PDFCell(Contract):
    row: NonnegativeInt
    column: NonnegativeInt
    colspan: PositiveInt = 1
    text: str
    word_ids: tuple[Identifier, ...]
    location: SourceLocation
    status: Literal["present", "empty"]


class PDFTableRow(Contract):
    source_row: PositiveInt
    values: tuple[str, ...]
    category: NonEmpty | None = None
    state: Literal["retained", "category", "excluded", "quarantined"]
    reason: NonEmpty | None = None


class PDFTablePart(Contract):
    physical_page: PositiveInt
    bbox: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
    header_origin: Literal["native_cells", "reviewed_column_roles", "unresolved_native_headers"]
    column_headers: tuple[NonEmpty, ...]
    cells: tuple[PDFCell, ...]
    rows: tuple[PDFTableRow, ...]
    continuation_of_page: PositiveInt | None = None


class PDFTable(Contract):
    table_id: Identifier
    block_id: Identifier | None
    parts: tuple[PDFTablePart, ...]
    state: Literal["pending", "quarantined"]


class PDFExtraction(Contract):
    extraction_id: Identifier
    parser_version: NonEmpty
    configuration_id: Identifier
    document: DocumentVersion
    comparison_only: bool = False
    blocks: tuple[ContentBlock, ...]
    words: tuple[PDFWord, ...]  # Administrative original evidence, including excluded words.
    pages: tuple[PDFPage, ...]
    layouts: tuple[PDFBlockLayout, ...]
    tables: tuple[PDFTable, ...]
    diagnostics: tuple[PDFDiagnostic, ...]
    review_state: Literal["pending", "quarantined"]

    @model_validator(mode="after")
    def validate_relationships(self):
        if len(self.pages) != self.document.page_count or [
            p.physical_page for p in self.pages
        ] != list(range(1, len(self.pages) + 1)):
            raise ValueError("PDF physical page map must cover the original document")
        words = {w.word_id: w for w in self.words}
        blocks = {b.block_id: b for b in self.blocks}
        if len(words) != len(self.words) or len(blocks) != len(self.blocks):
            raise ValueError("PDF evidence identities must be unique")
        for w in self.words:
            loc = w.location
            if (
                loc.format != "pdf"
                or loc.extraction_method != "native_pdf"
                or loc.bbox is None
                or loc.physical_page > len(self.pages)
            ):
                raise ValueError("native PDF words require valid page geometry")
            page = self.pages[loc.physical_page - 1]
            if loc.bbox[2] > page.width + 0.1 or loc.bbox[3] > page.height + 0.1:
                raise ValueError("word geometry exceeds physical page")
        if any(
            b.document_version_id != self.document.document_version_id or b.sequence_index != i
            for i, b in enumerate(self.blocks)
        ):
            raise ValueError("PDF blocks must resolve to the ordered original document version")
        if {layout.block_id for layout in self.layouts} != set(blocks) or len(self.layouts) != len(
            blocks
        ):
            raise ValueError("PDF block geometry must resolve to every retained block exactly once")
        for block in self.blocks:
            for location in block.source_locations:
                if (
                    location.format != "pdf"
                    or location.extraction_method != "native_pdf"
                    or location.bbox is None
                    or location.physical_page > len(self.pages)
                ):
                    raise ValueError("retained native blocks require original PDF page geometry")
                page = self.pages[location.physical_page - 1]
                if location.bbox[2] > page.width + 0.1 or location.bbox[3] > page.height + 0.1:
                    raise ValueError("retained block exceeds original page geometry")
        for layout in self.layouts:
            if not set(layout.word_ids) <= set(words):
                raise ValueError("PDF block refers to missing native words")
        for page in self.pages:
            if not set(page.omitted_word_ids) <= set(words):
                raise ValueError("omitted words must resolve to native evidence")
        for table in self.tables:
            if table.state == "quarantined" and (
                table.block_id is not None or self.review_state != "quarantined"
            ):
                raise ValueError(
                    "quarantined tables cannot resolve to retained blocks or a pending-only result"
                )
            if table.state == "pending" and (
                table.block_id not in blocks or blocks[table.block_id].block_type != "table"
            ):
                raise ValueError("retained PDF table must resolve to a table block")
            for part in table.parts:
                width = len(part.column_headers)
                if any(len(r.values) != width for r in part.rows):
                    raise ValueError("PDF row width differs from its contextual headers")
                if any(
                    not set(c.word_ids) <= set(words)
                    or c.location.physical_page != part.physical_page
                    for c in part.cells
                ):
                    raise ValueError("PDF table cells must preserve native word/page provenance")
        return self
