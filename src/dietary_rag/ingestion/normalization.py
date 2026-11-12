"""Shared deterministic cleaning and mask validation; no parsing, OCR or activation."""

import re
import unicodedata
from dataclasses import asdict

from dietary_rag.corpus.contracts import ContentBlock, SourceLocation, TableContext
from dietary_rag.corpus.identity import sha256_bytes, stable_id
from dietary_rag.ingestion.html_contracts import HTMLExtraction
from dietary_rag.ingestion.html_profiles import PROFILES as HTML_PROFILES
from dietary_rag.ingestion.html_profiles import REVIEWED_SELECTIONS
from dietary_rag.ingestion.normalization_contracts import (
    BlockDecision,
    ExclusionEvidence,
    NormalizationReview,
    NormalizedExtraction,
    NormalizedRow,
    NormalizedTable,
    Transformation,
)
from dietary_rag.ingestion.normalization_profiles import (
    COMMON_MASKS,
    LINE_WRAP_REPAIRS,
    SOURCE_MASKS,
)
from dietary_rag.ingestion.ocr_contracts import OCRExtraction
from dietary_rag.ingestion.pdf_contracts import PDFExtraction
from dietary_rag.ingestion.pdf_profiles import PROFILES as PDF_PROFILES
from dietary_rag.ingestion.pdf_profiles import REVIEWED_SOURCES

VERSION = "normalization_v1"
LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"})


class NormalizationError(ValueError):
    """Source provenance or a required normalization relationship failed."""


def clean_text(text, *, multiline=False, keep_indent=False):
    # NFC preserves fractions, temperatures, range dashes and typographic footnote markers.
    text = unicodedata.normalize("NFC", text).translate(LIGATURES)
    text = re.sub(r"(?<=[A-Za-z])\u00ad(?:\r?\n)?(?=[A-Za-z])", "", text)
    if multiline:
        return "\n".join(
            (
                " " * (len(line.expandtabs(2)) - len(line.expandtabs(2).lstrip()))
                if keep_indent
                else ""
            )
            + re.sub(r"[^\S\n]+", " ", line).strip()
            for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            if line.strip()
        )
    return re.sub(r"\s+", " ", text).strip()


def protected_facts(text):
    text = unicodedata.normalize("NFC", text).replace("’", "'")
    return (
        tuple(re.findall(r"\d+(?:[.,]\d+)*|[¼-¾\u2150-\u215e°%±−–]", text)),
        tuple(
            re.findall(
                r"\b(?:no|not|never|without|unless|except|only|don't|doesn't|cannot|can't|mustn't|mg|mcg|IU|minutes?|hours?|days?|weeks?|months?|years?|F|C)\b",
                text,
                re.I,
            )
        ),
    )


def parse_extraction(value):
    if isinstance(value, (HTMLExtraction, PDFExtraction, OCRExtraction)):
        return value
    if "adapter_version" in value:
        return OCRExtraction.model_validate(value)
    if "words" in value:
        return PDFExtraction.model_validate(value)
    return HTMLExtraction.model_validate(value)


def _inside(word, box):
    x0, y0, x1, y1 = word.location.bbox
    return box[0] <= (x0 + x1) / 2 < box[2] and box[1] <= (y0 + y1) / 2 < box[3]


def _identity(extraction):
    data = extraction.model_dump(mode="python", exclude={"extraction_id"})
    namespace = (
        "html_extraction"
        if isinstance(extraction, HTMLExtraction)
        else "pdf_extraction"
        if isinstance(extraction, PDFExtraction)
        else "ocr_extraction"
    )
    if isinstance(extraction, HTMLExtraction):
        data.pop(
            "review_state"
        )  # Native HTML's pending default was not part of its identity payload.
    if stable_id(namespace, data) != extraction.extraction_id:
        raise NormalizationError("original extraction identity differs from its artifact contents")


class Normalizer:
    def __init__(self, registry):
        self.registry = registry

    def normalize(self, extraction, *, artifact_sha256=None, review=None):
        extraction = parse_extraction(extraction)
        _identity(extraction)
        document = extraction.document
        name = document.document_id
        synthetic = (
            isinstance(extraction, OCRExtraction) and extraction.fixture_kind != "original_pdf"
        )
        if synthetic:
            if (
                document.rights_review.status != "project_created"
                or not document.eligibility.default_index
            ):
                raise NormalizationError(
                    "synthetic normalization requires labeled project-created provenance"
                )
            profile = None
        else:
            source = self.registry.get(name)
            comparison = isinstance(extraction, PDFExtraction) and extraction.comparison_only
            if not self.registry.consumer_eligibility(
                name
            ).eligible_for_consumer_ingestion and not (
                comparison
                and source.selection.approved_pdf_for_milestone
                and source.selection.decision == "approved_with_exclusions"
            ):
                raise NormalizationError(
                    "source is not approved consumer evidence or an explicit comparison"
                )
            fields = (
                "document_id",
                "family_id",
                "document_name",
                "publisher",
                "source_url",
                "resolved_url",
                "format",
                "dates",
                "rights_review",
                "content_role",
                "target_audience",
                "jurisdiction",
                "intended_audience",
                "source_tier",
                "author",
                "medical_reviewer",
            )
            if (
                any(getattr(document, f) != getattr(source, f) for f in fields)
                or document.raw_sha256 != source.access.sha256
                or document.eligibility.model_dump()
                != source.selection.model_dump(exclude={"approved_pdf_for_milestone"})
            ):
                raise NormalizationError(
                    "normalization source metadata/bytes/exclusions differ from current review"
                )
            if source.pdf and (
                document.page_count != source.pdf.page_count
                or document.text_classification != source.pdf.text_classification
            ):
                raise NormalizationError("PDF page metadata differs from current source review")
            if isinstance(extraction, HTMLExtraction):
                profile = HTML_PROFILES.get(name)
                if (
                    profile is None
                    or stable_id("mask_review", {"selection": source.selection})
                    != REVIEWED_SELECTIONS[name]
                ):
                    raise NormalizationError("HTML exclusion mask requires a new source review")
            else:
                profile = PDF_PROFILES.get(name)
                if (
                    profile is None
                    or stable_id("pdf_mask_review", {"selection": source.selection})
                    != REVIEWED_SOURCES[name]["selection_id"]
                    or document.raw_sha256 != REVIEWED_SOURCES[name]["raw_sha256"]
                ):
                    raise NormalizationError("PDF/OCR exclusion mask requires a new source review")
        adapter = (
            "html"
            if isinstance(extraction, HTMLExtraction)
            else "native_pdf"
            if isinstance(extraction, PDFExtraction)
            else "ocr"
        )
        config = stable_id(
            "normalization_config",
            {
                "version": VERSION,
                "unicode": unicodedata.unidata_version,
                "profile": asdict(profile) if profile else None,
                "eligibility": document.eligibility,
                "masks": COMMON_MASKS,
                "source_masks": SOURCE_MASKS.get(name, {}),
                "repairs": LINE_WRAP_REPAIRS.get(name, {}),
                "ligatures": LIGATURES.__repr__(),
            },
        )
        changes, decisions, exclusions, blocks, tables = [], [], [], [], []
        masks = {**COMMON_MASKS, **SOURCE_MASKS.get(name, {})}
        if profile:
            patterns = profile.excluded_text if adapter == "html" else profile.excluded_paragraphs
            masks.update({f"upstream_paragraph_{i}": p for i, p in enumerate(patterns)})
        compiled = {key: re.compile(value, re.I) for key, value in masks.items()}
        word_map = {w.word_id: w for w in extraction.words} if adapter != "html" else {}
        layouts = (
            {layout.block_id: layout for layout in extraction.layouts}
            if adapter == "native_pdf"
            else {}
        )
        native_tables = {
            t.block_id: t for t in getattr(extraction, "tables", ()) if t.block_id is not None
        }
        quarantine = extraction.review_state == "quarantined"

        def transform(value, block, field, multiline=False, keep_indent=False):
            after = clean_text(value, multiline=multiline, keep_indent=keep_indent)
            if protected_facts(value) != protected_facts(after):
                raise NormalizationError(
                    "cleaning would alter protected numbers, units or conditions"
                )
            if after != value:
                changes.append(
                    Transformation(
                        original_block_id=block.block_id,
                        field=field,
                        rule="unicode_whitespace",
                        before=value,
                        after=after,
                    )
                )
            return after

        for b in extraction.blocks:
            reasons = []
            state = "retained"
            inspected = "\n".join(
                (
                    b.text,
                    *(h for h in b.heading_path if h != document.document_name),
                    *(
                        [
                            b.table_context.title,
                            *b.table_context.column_headers,
                            *b.table_context.footnotes,
                        ]
                        if b.table_context
                        else []
                    ),
                )
            )
            if re.search(r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069]", inspected):
                reasons.append("ambiguous_invisible_or_directional_text")
                state = "quarantined"
            reasons.extend(key for key, pattern in compiled.items() if pattern.search(inspected))
            if name == "nhlbi_sodium_tips_pdf" and any(
                loc.physical_page == 2 for loc in b.source_locations
            ):
                reasons.append("nhlbi_individual_food_lookup_page")
            if (
                adapter == "html"
                and profile
                and any(s in b.heading_path for s in profile.excluded_sections)
            ):
                reasons.append("excluded_html_section")
            if adapter == "html" and profile.retained_text and b.block_type != "heading":
                body = re.sub(r"^(?:[-•+]\s+|\d+[.)]\s+)", "", b.text)
                if not any(re.search(pattern, body, re.I) for pattern in profile.retained_text):
                    reasons.append("outside_reviewed_paragraph_allowlist")
            if b.table_context and any(
                re.search(
                    r"\b(?:sodium|potassium|protein|vitamin|calcium|iron|fiber)\b", header, re.I
                )
                for header in b.table_context.column_headers
            ):
                reasons.append("individual_food_nutrient_table")
            layout = layouts.get(b.block_id)
            if layout and b.block_type != "table":
                for wid in layout.word_ids:
                    w = word_map[wid]
                    allowed = [
                        r
                        for r in profile.pages[w.location.physical_page - 1].regions
                        if r.key == layout.region_key
                    ]
                    if (
                        not any(_inside(w, r.bbox) for r in allowed)
                        or wid in extraction.pages[w.location.physical_page - 1].omitted_word_ids
                    ):
                        reasons.append("adjacent_column_or_excluded_region_leak")
                        state = "quarantined"
                        break
                original_words = " ".join(word_map[wid].text for wid in layout.word_ids)
                if re.sub(r"\s+", "", original_words) != re.sub(r"\s+", "", b.text):
                    reasons.append("native_text_word_link_mismatch")
                    state = "quarantined"
            if adapter == "native_pdf" and b.block_type == "table":
                t = native_tables[b.block_id]
                allowed = {
                    wid
                    for p in t.parts
                    for c in p.cells
                    if c.row == 0
                    or any(r.source_row == c.row and r.state == "retained" for r in p.rows)
                    for wid in c.word_ids
                }
                note_ids = {
                    wid
                    for wid in layout.word_ids
                    if any(
                        _inside(word_map[wid], loc.bbox)
                        and word_map[wid].location.physical_page == loc.physical_page
                        for loc in b.source_locations[len(t.parts) :]
                    )
                }
                if not set(layout.word_ids) <= allowed | note_ids:
                    reasons.append("excluded_table_cell_leak")
                    state = "quarantined"
            if b.block_type == "table":
                native_table = native_tables[b.block_id]
                rows = (
                    native_table.rows
                    if adapter == "html"
                    else tuple(
                        row
                        for part in native_table.parts
                        for row in part.rows
                        if row.state == "retained"
                    )
                )
                expected = [b.table_context.title]
                for row in rows:
                    headers = (
                        row.column_headers if adapter == "html" else b.table_context.column_headers
                    )
                    expected.append(
                        (f"Category: {row.category} | " if row.category else "")
                        + " | ".join(
                            f"{header}: {value}"
                            for header, value in zip(headers, row.values, strict=True)
                        )
                    )
                expected.extend(b.table_context.footnotes)
                if clean_text("\n".join(expected), multiline=True) != clean_text(
                    b.text, multiline=True
                ):
                    reasons.append("table_text_cell_or_qualifier_mismatch")
                    state = "quarantined"
            if adapter == "ocr":
                if any(
                    loc.review_state == "quarantined"
                    or loc.ocr_confidence is None
                    or loc.ocr_confidence < 85
                    for loc in b.source_locations
                ):
                    reasons.append("ocr_quality_gate")
                    state = "quarantined"
            if reasons:
                if state != "quarantined":
                    state = "excluded"
                if state == "quarantined":
                    quarantine = True
                for reason in dict.fromkeys(reasons):
                    exclusions.append(
                        ExclusionEvidence(
                            rule=reason,
                            origin="normalization",
                            reason="Entire block held or excluded; no partial recommendation repaired.",
                            locations=b.source_locations,
                            subject_id=b.block_id,
                        )
                    )
                decisions.append(
                    BlockDecision(
                        original_block_id=b.block_id,
                        original_text_sha256=sha256_bytes(b.text.encode()),
                        original_locations=b.source_locations,
                        state=state,
                        rules=tuple(dict.fromkeys(reasons)),
                    )
                )
                continue
            text = transform(
                b.text,
                b,
                "text",
                b.block_type in ("table", "recommendation_list"),
                b.block_type == "recommendation_list",
            )
            if layout and b.block_type != "table":
                ws = [word_map[w] for w in layout.word_ids]
                for old, new in LINE_WRAP_REPAIRS.get(name, {}).items():
                    left, right = old.split(" ")
                    pair = next(
                        (
                            (a.word_id, z.word_id)
                            for a, z in zip(ws, ws[1:], strict=False)
                            if a.text.endswith(left)
                            and z.text.startswith(right)
                            and a.location.physical_page == z.location.physical_page
                            and z.location.bbox[1] > a.location.bbox[1] + 3
                        ),
                        None,
                    )
                    if old in text and pair:
                        after = text.replace(old, new)
                        if protected_facts(text) != protected_facts(after):
                            raise NormalizationError("line-wrap repair would alter protected facts")
                        changes.append(
                            Transformation(
                                original_block_id=b.block_id,
                                field="text",
                                rule="reviewed_source_line_wrap",
                                before=text,
                                after=after,
                                word_ids=pair,
                            )
                        )
                        text = after
            context = None
            if b.table_context:
                c = b.table_context
                context = TableContext(
                    title=transform(c.title, b, "table.title"),
                    column_headers=tuple(
                        transform(v, b, f"table.header.{i}") for i, v in enumerate(c.column_headers)
                    ),
                    row_headers=tuple(
                        transform(v, b, f"table.row_header.{i}")
                        for i, v in enumerate(c.row_headers)
                    ),
                    units=tuple(transform(v, b, f"table.unit.{i}") for i, v in enumerate(c.units)),
                    footnotes=tuple(
                        transform(v, b, f"table.footnote.{i}", True)
                        for i, v in enumerate(c.footnotes)
                    ),
                )
            payload = dict(
                document_version_id=document.document_version_id,
                block_type=b.block_type,
                heading_path=tuple(
                    transform(v, b, f"heading.{i}") for i, v in enumerate(b.heading_path)
                ),
                sequence_index=len(blocks),
                text=text,
                source_locations=tuple(
                    loc.model_copy(update={"review_state": "pending"}) for loc in b.source_locations
                ),
                table_context=context,
            )
            canonical = ContentBlock(
                block_id=stable_id(
                    "normalized_block", {"config": config, "original": b.block_id, **payload}
                ),
                **payload,
            )
            blocks.append(canonical)
            decisions.append(
                BlockDecision(
                    original_block_id=b.block_id,
                    original_text_sha256=sha256_bytes(b.text.encode()),
                    original_locations=b.source_locations,
                    normalized_block_id=canonical.block_id,
                    state="retained",
                    rules=("source_mask_and_fidelity_passed",),
                )
            )
            if b.block_type == "table":
                t = native_tables[b.block_id]
                rows = []
                parts = (
                    [(None, t.rows)]
                    if adapter == "html"
                    else [(p.physical_page, p.rows) for p in t.parts]
                )
                for page, data_rows in parts:
                    for row in data_rows:
                        if adapter != "html" and row.state != "retained":
                            continue
                        rows.append(
                            NormalizedRow(
                                source_row=row.source_row,
                                physical_page=page,
                                category=transform(
                                    row.category, b, f"row.{page}.{row.source_row}.category"
                                )
                                if row.category
                                else None,
                                column_headers=tuple(
                                    transform(h, b, f"row.{page}.{row.source_row}.header.{i}")
                                    for i, h in enumerate(
                                        row.column_headers
                                        if adapter == "html"
                                        else context.column_headers
                                    )
                                ),
                                values=tuple(
                                    transform(v, b, f"row.{page}.{row.source_row}.value.{i}")
                                    for i, v in enumerate(row.values)
                                ),
                            )
                        )
                tables.append(
                    NormalizedTable(
                        block_id=canonical.block_id,
                        original_table_id=t.block_id if adapter == "html" else t.table_id,
                        context=context,
                        rows=tuple(rows),
                    )
                )
        # Preserve upstream omission reasons and location links without copying excluded text.
        if adapter == "html":
            for d in extraction.diagnostics:
                if d.code in (
                    "excluded_text",
                    "excluded_section",
                    "outside_text_allowlist",
                    "furniture_removed",
                    "hidden_removed",
                    "source_duplicate_removed",
                    "navigation_removed",
                ):
                    loc = (
                        (
                            SourceLocation(
                                format="html", extraction_method="html", dom_path=d.dom_path
                            ),
                        )
                        if d.dom_path
                        else ()
                    )
                    exclusions.append(
                        ExclusionEvidence(
                            rule=d.code,
                            origin="native_extraction",
                            reason="Original DOM omission recorded by reviewed extraction profile.",
                            locations=loc,
                        )
                    )
            exclusions.append(
                ExclusionEvidence(
                    rule="visual_media_omitted",
                    origin="native_extraction",
                    reason="HTML images and unlicensed media were not converted into guidance.",
                )
            )
        elif adapter == "native_pdf":
            for d in extraction.diagnostics:
                if d.code in (
                    "excluded_paragraph",
                    "excluded_table_row",
                    "outside_reviewed_regions",
                    "visual_only_omitted",
                    "repeated_furniture_removed",
                ):
                    box = d.bbox or (
                        0,
                        0,
                        extraction.pages[d.physical_page - 1].width,
                        extraction.pages[d.physical_page - 1].height,
                    )
                    loc = (
                        SourceLocation(
                            format="pdf",
                            physical_page=d.physical_page,
                            bbox=box,
                            extraction_method="native_pdf",
                        ),
                    )
                    exclusions.append(
                        ExclusionEvidence(
                            rule=d.code,
                            origin="native_extraction",
                            reason=d.detail
                            if d.code in ("outside_reviewed_regions", "visual_only_omitted")
                            else "Original reviewed paragraph/table/furniture omission; original artifact retains the comparison evidence.",
                            locations=loc,
                        )
                    )
            for native_table in extraction.tables:
                if native_table.state == "quarantined":
                    exclusions.append(
                        ExclusionEvidence(
                            rule="unresolved_native_table",
                            origin="native_extraction",
                            reason="Native cells/headers are unresolved; no canonical table released.",
                            subject_id=native_table.table_id,
                            locations=tuple(
                                SourceLocation(
                                    format="pdf",
                                    physical_page=part.physical_page,
                                    bbox=part.bbox,
                                    extraction_method="native_pdf",
                                    review_state="quarantined",
                                )
                                for part in native_table.parts
                            ),
                        )
                    )
        else:
            for c in extraction.candidates:
                if c.state == "quarantined":
                    exclusions.append(
                        ExclusionEvidence(
                            rule="ocr_quality_gate",
                            origin="ocr_quality",
                            reason="Uncertain OCR candidate unavailable as canonical evidence.",
                            locations=(c.source_location,),
                            subject_id=c.candidate_id,
                        )
                    )
        payload = dict(
            normalizer_version=VERSION,
            configuration_id=config,
            original_extraction_id=extraction.extraction_id,
            original_artifact_sha256=artifact_sha256
            or sha256_bytes(extraction.model_dump_json().encode()),
            document=document,
            format_adapter=adapter,
            comparison_only=getattr(extraction, "comparison_only", False),
            fixture_kind=getattr(extraction, "fixture_kind", None),
            blocks=tuple(blocks),
            tables=tuple(tables),
            decisions=tuple(decisions),
            transformations=tuple(changes),
            exclusions=tuple(exclusions),
            review_state="quarantined" if quarantine else "pending",
        )
        result = NormalizedExtraction(
            normalization_id=stable_id("normalization", payload), **payload
        )
        if review is not None:
            review = NormalizationReview.model_validate(review)
            if result.review_state == "quarantined":
                raise NormalizationError("unresolved normalization cannot be marked reviewed")
            result = NormalizedExtraction.model_validate(
                {
                    **result.model_dump(),
                    "review_state": "reviewed",
                    "review": review,
                    "blocks": tuple(
                        b.model_copy(
                            update={
                                "source_locations": tuple(
                                    loc.model_copy(update={"review_state": "reviewed"})
                                    for loc in b.source_locations
                                )
                            }
                        )
                        for b in result.blocks
                    ),
                }
            )
        return result
