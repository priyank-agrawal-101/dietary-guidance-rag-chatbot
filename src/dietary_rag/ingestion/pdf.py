"""Reviewed native PDF layouts, explicit tables and original-page provenance."""

import re
from collections import Counter
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from sys import version_info

from dietary_rag.corpus.contracts import ContentBlock, DocumentVersion, SourceLocation, TableContext
from dietary_rag.corpus.identity import sha256_bytes, stable_id
from dietary_rag.corpus.registry import LOCAL_TEXT_RIGHTS, SourceRegistry
from dietary_rag.ingestion.pdf_contracts import (
    PDFBlockLayout,
    PDFCell,
    PDFDiagnostic,
    PDFExtraction,
    PDFPage,
    PDFTable,
    PDFTablePart,
    PDFTableRow,
    PDFWord,
)
from dietary_rag.ingestion.pdf_profiles import PROFILES, REVIEWED_SOURCES, PDFProfile

PARSER_VERSION = "native-pdf-1"


class PDFExtractionError(ValueError):
    """Native evidence requires an approved layout or an explicit quarantine."""


def _inside(word, box):
    x0, y0, x1, y1 = word.location.bbox
    return box[0] <= (x0 + x1) / 2 < box[2] and box[1] <= (y0 + y1) / 2 < box[3]


def _bbox(words):
    boxes = [w.location.bbox for w in words]
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _lines(words):
    groups = []
    for word in sorted(words, key=lambda w: (w.location.bbox[1], w.location.bbox[0])):
        if not groups or abs(word.location.bbox[1] - groups[-1][0].location.bbox[1]) > 3:
            groups.append([word])
        else:
            groups[-1].append(word)
    return [sorted(g, key=lambda w: w.location.bbox[0]) for g in groups]


def _text(words):
    return "\n".join(" ".join(w.text for w in line) for line in _lines(words))


def _location(page, label, box, state="pending"):
    return SourceLocation(
        format="pdf",
        physical_page=page,
        printed_page_label=label,
        bbox=tuple(round(float(v), 4) for v in box),
        extraction_method="native_pdf",
        review_state=state,
    )


def _paragraphs(words, threshold, forced_kind=None):
    groups = []
    for line in _lines(words):
        text = " ".join(w.text for w in line)
        heading = min(w.font_size for w in line) >= threshold
        bullet = bool(re.match(r"^(?:[▪•+\-]|\d+[.)])(?:\s|$)", text))
        kind = forced_kind or (
            "heading" if heading else "recommendation_list" if bullet else "paragraph"
        )
        if groups:
            previous = groups[-1]
            gap = _bbox(line)[1] - _bbox(previous[1])[-1]
            if not bullet and kind == previous[0] and gap < 9:
                previous[1].extend(line)
                continue
            if not heading and not bullet and previous[0] == "recommendation_list" and gap < 9:
                previous[1].extend(line)
                continue
        groups.append([kind, list(line)])
    return [(kind, re.sub(r"\s+", " ", _text(group)).strip(), group) for kind, group in groups]


def _printed_label(reader, page_index, words, height, width):
    footer = [w for w in words if w.location.bbox[1] > height * 0.88]
    candidates = []
    for line in _lines(footer):
        text = " ".join(w.text for w in line)
        match = re.search(r"(?:^|\|\s*)(\d+|[ivxlcdm]+)$", text, re.I)
        if match and (text == match.group(1) or "|" in text):
            candidates.append(match.group(1))
    for word in footer:
        if re.fullmatch(r"\d{1,3}|[ivxlcdm]+", word.text, re.I) and (
            word.location.bbox[0] > width * 0.88
            or (
                width * 0.43 < word.location.bbox[0] < width * 0.57
                and word.location.bbox[1] > height * 0.94
            )
        ):
            candidates.append(word.text)
    values = set(candidates)
    return (candidates[-1], "visible_footer") if len(values) == 1 else (None, "unknown")


def _protected_footer(value):
    return bool(
        re.search(
            r"created|modified|revis|warning|safety|quality|temperature|copyright|"
            r"January|February|March|April|May|June|July|August|September|October|November|December",
            value,
            re.I,
        )
    )


class PDFExtractor:
    def __init__(self, registry: SourceRegistry, document_id: str, *, comparison=False):
        self.source = registry.get(document_id)
        source = self.source
        eligible = registry.consumer_eligibility(document_id).eligible_for_consumer_ingestion
        approved_comparison = (
            comparison
            and source.selection.approved_pdf_for_milestone
            and source.selection.decision == "approved_with_exclusions"
            and source.content_role == "consumer_guidance"
            and source.rights_review.status in LOCAL_TEXT_RIGHTS
            and source.target_audience == "US"
            and (source.jurisdiction == "US" or source.jurisdiction.startswith("US-"))
        )
        if not eligible and not approved_comparison:
            raise PDFExtractionError(
                "source is not approved consumer guidance or an explicitly selected approved PDF comparison"
            )
        if (
            document_id not in PROFILES
            or source.format != "pdf"
            or source.pdf is None
            or source.parser_profile != REVIEWED_SOURCES[document_id]["parser_profile"]
        ):
            raise PDFExtractionError("no reviewed native PDF layout for this source")
        review = REVIEWED_SOURCES[document_id]
        if (
            source.access.sha256 != review["raw_sha256"]
            or stable_id("pdf_mask_review", {"selection": source.selection})
            != review["selection_id"]
        ):
            raise PDFExtractionError(
                "reviewed PDF bytes or selection changed; layout/mask review required"
            )
        self.profile = PROFILES[document_id]
        self.comparison_only = not eligible

    def extract(self, raw_path: Path, document: DocumentVersion) -> tuple[ContentBlock, ...]:
        result = self.extract_report(raw_path, document)
        if result.review_state == "quarantined":
            raise PDFExtractionError(
                "PDF extraction has quarantined native regions; inspect extract_report"
            )
        return result.blocks

    def extract_report(self, raw_path: Path, document: DocumentVersion) -> PDFExtraction:
        source = self.source
        fields = (
            "document_id",
            "family_id",
            "document_name",
            "publisher",
            "source_url",
            "resolved_url",
            "format",
            "dates",
            "source_tier",
            "jurisdiction",
            "target_audience",
            "intended_audience",
            "content_role",
            "rights_review",
            "author",
            "medical_reviewer",
        )
        if any(
            getattr(document, f) != getattr(source, f) for f in fields
        ) or document.eligibility.model_dump() != source.selection.model_dump(
            exclude={"approved_pdf_for_milestone"}
        ):
            raise PDFExtractionError("PDF document metadata differs from the reviewed registry")
        if (
            document.raw_sha256 != source.access.sha256
            or document.page_count != source.pdf.page_count
            or document.text_classification != source.pdf.text_classification
        ):
            raise PDFExtractionError(
                "PDF acquisition bytes/page classification differ from source review"
            )
        return _extract_native(
            raw_path, document, self.profile, comparison_only=self.comparison_only
        )

    def extract_reviewed_file(self, raw_path: Path, evidence_path: Path) -> PDFExtraction:
        """Explicit comparison path; validate the existing receipt without catalog writes."""
        from dietary_rag.corpus.contracts import SourceEligibility
        from dietary_rag.ingestion.acquisition import SourceEvidence

        if not self.comparison_only:
            raise PDFExtractionError(
                "reviewed-file path is reserved for nondefault approved comparisons"
            )
        source = self.source
        receipt = SourceEvidence.model_validate_json(evidence_path.read_text())
        content = raw_path.read_bytes()
        if (
            receipt.source_id != source.document_id
            or receipt.requested_url != source.source_url
            or receipt.final_url != source.resolved_url
            or receipt.checked_at != source.access.checked_at
            or receipt.status != 200
            or receipt.format != "pdf"
            or receipt.sha256 != source.access.sha256
            or sha256_bytes(content) != receipt.sha256
            or receipt.bytes != len(content)
            or (receipt.retrieved_at and receipt.retrieved_at > receipt.checked_at)
        ):
            raise PDFExtractionError("PDF comparison bytes/receipt differ from reviewed source")
        document = DocumentVersion(
            **source.model_dump(
                include={
                    "document_id",
                    "family_id",
                    "document_name",
                    "publisher",
                    "source_url",
                    "resolved_url",
                    "format",
                    "dates",
                    "source_tier",
                    "jurisdiction",
                    "target_audience",
                    "intended_audience",
                    "content_role",
                    "rights_review",
                    "author",
                    "medical_reviewer",
                }
            ),
            document_version_id=stable_id(
                "docv_pdf_comparison", {"source": source, "receipt": receipt}
            ),
            retrieved_at=receipt.retrieved_at or receipt.checked_at,
            raw_sha256=receipt.sha256,
            page_count=source.pdf.page_count,
            text_classification=source.pdf.text_classification,
            eligibility=SourceEligibility.model_validate(
                source.selection.model_dump(exclude={"approved_pdf_for_milestone"})
            ),
        )
        return self.extract_report(raw_path, document)


def _extract_native(
    raw_path: Path, document: DocumentVersion, profile: PDFProfile, *, comparison_only=False
):
    """Low-level engine for the reviewed adapter and project-created layout fixtures."""
    import pdfminer
    import pdfplumber
    import pypdf
    from pypdf import PdfReader

    content = raw_path.read_bytes()
    if (
        document.format != "pdf"
        or not content.startswith(b"%PDF-")
        or sha256_bytes(content) != document.raw_sha256
    ):
        raise PDFExtractionError("PDF signature or original SHA-256 differs from acquisition")
    try:
        reader = PdfReader(BytesIO(content), strict=True)
    except Exception as exc:
        raise PDFExtractionError("cannot open native PDF") from exc
    if reader.is_encrypted:
        raise PDFExtractionError("encrypted PDF requires a separate approved acquisition")
    if (
        len(reader.pages) != document.page_count
        or len(profile.pages) != document.page_count
        or document.page_count > 100
    ):
        raise PDFExtractionError(
            "physical page count differs from reviewed layout/acquisition or exceeds 100"
        )
    config = stable_id(
        "pdf_config",
        {
            "parser": PARSER_VERSION,
            "profile": asdict(profile),
            "pdfplumber": pdfplumber.__version__,
            "pypdf": pypdf.__version__,
            "pdfminer": pdfminer.__version__,
            "python": f"{version_info.major}.{version_info.minor}",
        },
    )
    all_words, pages, diagnostics, drafts, native_tables = [], [], [], [], []
    table_groups = {}
    continuation_groups = {}
    quarantined = False

    def diagnostic(code, detail, pn=None, box=None):
        diagnostics.append(PDFDiagnostic(code=code, detail=detail, physical_page=pn, bbox=box))

    def draft(kind, text, words, section, region, locations=None, context=None):
        if not text:
            return None
        item = {
            "block_type": kind,
            "text": text,
            "words": list(words),
            "heading_path": tuple(dict.fromkeys((document.document_name, *section))),
            "region_key": region,
            "locations": locations
            or (words[0].location.model_copy(update={"bbox": _bbox(words)}),),
            "table_context": context,
        }
        drafts.append(item)
        return item

    with pdfplumber.open(BytesIO(content)) as pdf:
        # Duplicate painted characters are layout artifacts, not repeated recommendations.
        prepared = []
        for pn, page in enumerate(pdf.pages, 1):
            clean = page.dedupe_chars(tolerance=1, extra_attrs=())
            removed = len(page.chars) - len(clean.chars)
            if removed:
                diagnostic("duplicate_characters_removed", str(removed), pn)
            words = []
            for w in clean.extract_words(extra_attrs=["fontname", "size"]):
                box = tuple(float(w[k]) for k in ("x0", "top", "x1", "bottom"))
                if min(box) < 0 or box[2] > page.width + 0.1 or box[3] > page.height + 0.1:
                    raise PDFExtractionError(
                        "native word extends outside physical page; review required"
                    )
                loc = _location(pn, None, box)
                payload = {
                    "page": pn,
                    "bbox": loc.bbox,
                    "text": w["text"],
                    "font": w["fontname"],
                    "size": w["size"],
                    "ordinal": len(words),
                    "document_version_id": document.document_version_id,
                }
                words.append(
                    PDFWord(
                        word_id=stable_id("pdf_word", payload),
                        text=w["text"],
                        location=loc,
                        font_name=w["fontname"],
                        font_size=w["size"],
                    )
                )
            label, origin = _printed_label(reader, pn - 1, words, page.height, page.width)
            words = [
                w.model_copy(
                    update={"location": w.location.model_copy(update={"printed_page_label": label})}
                )
                for w in words
            ]
            prepared.append((clean, words, label, origin))
        margin_counts = Counter()
        for page, words, _, _ in prepared:
            for line in _lines(
                [
                    w
                    for w in words
                    if w.location.bbox[1] < page.height * 0.045
                    or w.location.bbox[1] > page.height * 0.94
                ]
            ):
                value = re.sub(r"\d+", "{n}", _text(line)).strip()
                if not _protected_footer(value):
                    margin_counts[value] += 1
        for pn, ((page, words, label, origin), page_profile) in enumerate(
            zip(prepared, profile.pages, strict=True), 1
        ):
            if len(all_words) + len(words) > 100000:
                raise PDFExtractionError("native PDF exceeds 100000-word bound")
            all_words.extend(words)
            diagnostic(
                "page_layout",
                f"{len(page_profile.regions)} ordered text regions; {len(page_profile.tables)} expected guidance table parts",
                pn,
            )
            diagnostic(
                "visual_only_omitted",
                f"{len(page.images)} raster images, {len(page.curves)} curves and {len(page.rects)} rectangles observed; images, logos and visual-only guidance are omitted. Only reviewed table geometry is interpreted.",
                pn,
            )
            auto = page.find_tables()
            diagnostic(
                "default_table_detection",
                f"{len(auto)} candidates; reviewed geometry decides which are guidance tables",
                pn,
            )
            if (
                profile.strict_unprofiled_tables
                and not page_profile.tables
                and any(len(t.rows) > 1 and len(t.rows[0].cells) > 1 for t in auto)
            ):
                raise PDFExtractionError(
                    "unreviewed visible table cannot be flattened into native prose"
                )
            furniture = set()
            for line in _lines(
                [
                    w
                    for w in words
                    if w.location.bbox[1] < page.height * 0.045
                    or w.location.bbox[1] > page.height * 0.94
                ]
            ):
                value = re.sub(r"\d+", "{n}", _text(line)).strip()
                if margin_counts[value] >= 2:
                    furniture.update(w.word_id for w in line)
                    diagnostic("repeated_furniture_removed", _text(line), pn, _bbox(line))
            consumed = set(furniture)
            table_words = set()
            table_items = []
            for spec in page_profile.tables:
                box = spec.bbox
                if (
                    len(spec.columns) < 3
                    or len(spec.boundaries) < 3
                    or len(spec.expected_headers) != len(spec.columns) - 1
                ):
                    raise PDFExtractionError(
                        "reviewed table requires complete column roles and row boundaries"
                    )
                if spec.after_region and spec.after_region not in {
                    region.key for region in page_profile.regions
                }:
                    raise PDFExtractionError(
                        "table ordering refers to a missing reviewed text region"
                    )
                if (
                    any(box[i] < 0 for i in (0, 1))
                    or box[2] > page.width
                    or box[3] > page.height
                    or tuple(sorted(set(spec.columns))) != spec.columns
                    or tuple(sorted(set(spec.boundaries))) != spec.boundaries
                ):
                    raise PDFExtractionError("invalid reviewed table geometry")
                settings = {
                    "vertical_strategy": "explicit",
                    "horizontal_strategy": "explicit",
                    "explicit_vertical_lines": list(spec.columns),
                    "explicit_horizontal_lines": list(spec.boundaries),
                    "snap_tolerance": 0.1,
                    "join_tolerance": 0.1,
                    "intersection_tolerance": 1,
                }
                found = page.crop(box).find_tables(table_settings=settings)
                if len(found) != 1:
                    raise PDFExtractionError(
                        "expected guidance table was not detected with reviewed geometry"
                    )
                diagnostic(
                    "explicit_table_geometry",
                    "Restored source-aligned columns and row boundaries",
                    pn,
                    box,
                )
                width = len(spec.columns) - 1
                cells = []
                rows = []
                category = None
                ambiguous = False
                for row, (top, bottom) in enumerate(
                    zip(spec.boundaries, spec.boundaries[1:], strict=False)
                ):
                    vals = []
                    boxes = (
                        [(spec.columns[0], top, spec.columns[-1], bottom)]
                        if row == 0 and spec.header_span
                        else [
                            (left, top, right, bottom)
                            for left, right in zip(spec.columns, spec.columns[1:], strict=False)
                        ]
                    )
                    row_words = []
                    for col, cell_box in enumerate(boxes):
                        members = [w for w in words if _inside(w, cell_box)]
                        row_words.extend(members)
                        table_words.update(w.word_id for w in members)
                        value = re.sub(r"\s+", " ", _text(members)).strip()
                        vals.append(value)
                        if any(
                            w.location.bbox[0] < cell_box[0] - 0.5
                            or w.location.bbox[2] > cell_box[2] + 0.5
                            or w.location.bbox[1] < cell_box[1] - 0.5
                            or w.location.bbox[3] > cell_box[3] + 0.5
                            for w in members
                        ):
                            ambiguous = True
                            diagnostic(
                                "ambiguous_table_cell",
                                "Native word crosses a reviewed cell boundary; table quarantined",
                                pn,
                                cell_box,
                            )
                        cells.append(
                            PDFCell(
                                row=row,
                                column=col,
                                colspan=width if row == 0 and spec.header_span else 1,
                                text=value,
                                word_ids=tuple(w.word_id for w in members),
                                location=_location(pn, label, cell_box),
                                status="present" if members else "empty",
                            )
                        )
                    if row == 0:
                        headers = spec.expected_headers if spec.header_span else tuple(vals)
                        header_origin = (
                            "reviewed_column_roles" if spec.header_span else "native_cells"
                        )
                        if not spec.header_span and tuple(vals) != spec.expected_headers:
                            headers = spec.expected_headers
                            header_origin = "unresolved_native_headers"
                            ambiguous = True
                            diagnostic(
                                "ambiguous_table_headers",
                                f"Native headers {vals!r} differ from reviewed headers",
                                pn,
                                box,
                            )
                        title = vals[0] if spec.header_span else document.document_name
                        continue
                    state = "retained"
                    reason = None
                    if spec.category_rows and vals[0] and not any(vals[1:]):
                        category = vals[0]
                        state = "category"
                    elif (
                        spec.standalone_x
                        and row_words
                        and min(w.location.bbox[0] for w in row_words) < spec.standalone_x
                    ):
                        category = None
                    if any(
                        re.search(pattern, " ".join(vals), re.I) for pattern in spec.excluded_rows
                    ):
                        state = "excluded"
                        reason = "Reviewed row exclusion: incomplete qualification or out-of-scope content."
                        diagnostic("excluded_table_row", " | ".join(vals), pn, boxes[0])
                    rows.append(
                        PDFTableRow(
                            source_row=row,
                            values=tuple(vals),
                            category=category,
                            state=state,
                            reason=reason,
                        )
                    )
                part = PDFTablePart(
                    physical_page=pn,
                    bbox=box,
                    header_origin=header_origin,
                    column_headers=headers,
                    cells=tuple(cells),
                    rows=tuple(rows),
                )
                notes = [
                    re.sub(r"\s+", " ", _text([w for w in words if _inside(w, nbox)])).strip()
                    for nbox in spec.notes
                ]
                if spec.header_span:
                    notes.append(title)
                context = TableContext(
                    title=title,
                    column_headers=headers,
                    row_headers=tuple(
                        dict.fromkeys(
                            r.category or r.values[0]
                            for r in rows
                            if r.state == "retained" and (r.category or r.values[0])
                        )
                    ),
                    units=tuple(
                        dict.fromkeys(
                            re.findall(r"\([^)]*(?:°|Days|Months)[^)]*\)", " ".join(headers), re.I)
                        )
                    ),
                    footnotes=tuple(dict.fromkeys(n for n in notes if n)),
                )
                key = spec.continuation_key
                if key and key in table_groups:
                    existing = table_groups[key]
                    if existing["context"].column_headers != context.column_headers:
                        ambiguous = True
                        diagnostic(
                            "ambiguous_table_continuation",
                            "Continuation headers differ; table quarantined",
                            pn,
                            box,
                        )
                    part = part.model_copy(
                        update={"continuation_of_page": existing["parts"][-1].physical_page}
                    )
                    existing["parts"].append(part)
                    existing["words"].extend(w for w in words if _inside(w, box))
                    existing["ambiguous"] |= ambiguous
                    existing["note_boxes"].extend((pn, box) for box in spec.notes)
                    existing["context"] = existing["context"].model_copy(
                        update={
                            "footnotes": tuple(
                                dict.fromkeys((*existing["context"].footnotes, *context.footnotes))
                            ),
                            "row_headers": tuple(
                                dict.fromkeys(
                                    (*existing["context"].row_headers, *context.row_headers)
                                )
                            ),
                        }
                    )
                else:
                    existing = {
                        "parts": [part],
                        "context": context,
                        "words": [w for w in words if _inside(w, box)],
                        "ambiguous": ambiguous,
                        "key": spec.key,
                        "after_region": spec.after_region,
                        "note_boxes": [(pn, box) for box in spec.notes],
                        "draft": None,
                    }
                    native_tables.append(existing)
                    if key:
                        table_groups[key] = existing
                    table_items.append(existing)
            consumed.update(table_words)
            assignments = set()
            for reg in page_profile.regions:
                x0, y0, x1, y1 = reg.bbox
                if not (0 <= x0 < x1 <= page.width and 0 <= y0 < y1 <= page.height):
                    raise PDFExtractionError("reviewed text region exceeds original physical page")
                members = [
                    w
                    for w in words
                    if _inside(w, reg.bbox) and w.word_id not in table_words | furniture
                ]
                ids = {w.word_id for w in members}
                if ids & assignments:
                    raise PDFExtractionError("reviewed reading regions overlap native words")
                assignments.update(ids)
                consumed.update(ids)
                if not members:
                    quarantined = True
                    diagnostic(
                        "no_native_region",
                        "Expected content region has no native words; review/OCR routing required",
                        pn,
                        reg.bbox,
                    )
                    continue
                heading = (reg.section,) if reg.section else ()
                for kind, text, group in _paragraphs(members, profile.heading_min_size, reg.kind):
                    if any(
                        re.search(pattern, text, re.I) for pattern in profile.excluded_paragraphs
                    ):
                        diagnostic("excluded_paragraph", text, pn, _bbox(group))
                        continue
                    if kind == "heading":
                        heading = tuple(
                            dict.fromkeys((*((reg.section,) if reg.section else ()), text))
                        )
                    item = draft(kind, text, group, heading, reg.key)
                    if reg.continuation_key:
                        old = continuation_groups.get(reg.continuation_key)
                        if old is not None and old is not item:
                            if (
                                old["block_type"] != kind
                                or old["heading_path"] != item["heading_path"]
                            ):
                                raise PDFExtractionError(
                                    "explicit prose continuation has incompatible type or heading context"
                                )
                            old["text"] += " " + text
                            old["words"].extend(group)
                            old["locations"] += item["locations"]
                            drafts.remove(item)
                        else:
                            continuation_groups[reg.continuation_key] = item
                diagnostic("text_region", reg.key, pn, reg.bbox)
                for table_item in table_items:
                    if table_item["draft"] is None and table_item["after_region"] == reg.key:
                        table_item["draft"] = draft(
                            "table",
                            "pending table",
                            table_item["words"],
                            (),
                            table_item["key"],
                            context=table_item["context"],
                            locations=(_location(pn, label, table_item["parts"][0].bbox),),
                        )
            # Place a table after this page's prose regions; geometry retains its true location.
            for item in table_items:
                if item["draft"] is not None:
                    continue
                placeholder = draft(
                    "table",
                    "pending table",
                    item["words"],
                    (),
                    item["key"],
                    context=item["context"],
                    locations=(_location(pn, label, item["parts"][0].bbox),),
                )
                item["draft"] = placeholder
            omitted = tuple(w.word_id for w in words if w.word_id not in consumed)
            diagnostic("outside_reviewed_regions", page_profile.omission, pn)
            status = (
                "excluded"
                if not page_profile.regions and not page_profile.tables
                else "native"
                if words
                else "no_native_text"
            )
            pages.append(
                PDFPage(
                    physical_page=pn,
                    printed_page_label=label,
                    pdf_page_label=reader.page_labels[pn - 1]
                    if "/PageLabels" in reader.trailer["/Root"]
                    else None,
                    label_origin=origin,
                    width=page.width,
                    height=page.height,
                    rotation=int(page.rotation),
                    native_word_count=len(words),
                    image_count=len(page.images),
                    omitted_word_ids=omitted,
                    status=status,
                )
            )
    for table in native_tables:
        if table["ambiguous"]:
            quarantined = True
            if table["draft"] in drafts:
                drafts.remove(table["draft"])
            continue
        parts = table["parts"]
        context = table["context"]
        lines = [context.title]
        for part in parts:
            for row in part.rows:
                if row.state == "retained":
                    lines.append(
                        (f"Category: {row.category} | " if row.category else "")
                        + " | ".join(
                            f"{h}: {v}"
                            for h, v in zip(context.column_headers, row.values, strict=True)
                        )
                    )
        lines.extend(context.footnotes)
        table["draft"]["text"] = "\n".join(lines)
        table["draft"]["locations"] = tuple(
            _location(p.physical_page, pages[p.physical_page - 1].printed_page_label, p.bbox)
            for p in parts
        )
        retained_ids = {
            word_id
            for part in parts
            for cell in part.cells
            if cell.row == 0
            or any(row.source_row == cell.row and row.state == "retained" for row in part.rows)
            for word_id in cell.word_ids
        }
        note_words = [
            word
            for word in all_words
            if any(
                word.location.physical_page == pn and _inside(word, box)
                for pn, box in table["note_boxes"]
            )
        ]
        table["draft"]["words"] = [
            word for word in table["words"] if word.word_id in retained_ids
        ] + note_words
        table["draft"]["locations"] += tuple(
            _location(pn, pages[pn - 1].printed_page_label, box) for pn, box in table["note_boxes"]
        )
    blocks = []
    layouts = []
    for index, item in enumerate(drafts):
        payload = dict(
            document_version_id=document.document_version_id,
            block_type=item["block_type"],
            heading_path=item["heading_path"],
            sequence_index=index,
            text=item["text"],
            source_locations=item["locations"],
            table_context=item["table_context"],
        )
        block = ContentBlock(block_id=stable_id("block", {"config": config, **payload}), **payload)
        blocks.append(block)
        item["block_id"] = block.block_id
        layouts.append(
            PDFBlockLayout(
                block_id=block.block_id,
                region_key=item["region_key"],
                word_ids=tuple(w.word_id for w in item["words"]),
            )
        )
    tables = []
    for item in native_tables:
        parts = tuple(item["parts"])
        tables.append(
            PDFTable(
                table_id=stable_id(
                    "pdf_table",
                    {"document": document.document_version_id, "config": config, "parts": parts},
                ),
                block_id=None if item["ambiguous"] else item["draft"]["block_id"],
                parts=parts,
                state="quarantined" if item["ambiguous"] else "pending",
            )
        )
    retained_ids = {word_id for layout in layouts for word_id in layout.word_ids}
    pages = [
        page.model_copy(
            update={
                "omitted_word_ids": tuple(
                    word.word_id
                    for word in all_words
                    if word.location.physical_page == page.physical_page
                    and word.word_id not in retained_ids
                )
            }
        )
        for page in pages
    ]
    if not blocks and not quarantined:
        raise PDFExtractionError("no retained native guidance in selected PDF")
    diagnostic(
        "review_pending",
        "Native layout extraction only. Original words/cells can include excluded content; consumers must use retained blocks. Normalization, OCR and activation are separate gates.",
    )
    payload = dict(
        parser_version=PARSER_VERSION,
        configuration_id=config,
        document=document,
        comparison_only=comparison_only,
        blocks=tuple(blocks),
        words=tuple(all_words),
        pages=tuple(pages),
        layouts=tuple(layouts),
        tables=tuple(tables),
        diagnostics=tuple(diagnostics),
        review_state="quarantined" if quarantined else "pending",
    )
    return PDFExtraction(extraction_id=stable_id("pdf_extraction", payload), **payload)
