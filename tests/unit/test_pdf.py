"""Portable source-derived text layouts and project-created PDF edge cases."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from reportlab.pdfgen import canvas

from dietary_rag.corpus.contracts import DocumentVersion, SourceEligibility
from dietary_rag.corpus.identity import sha256_bytes
from dietary_rag.ingestion.pdf import PDFExtractionError, PDFExtractor, _extract_native
from dietary_rag.ingestion.pdf_contracts import PDFExtraction
from dietary_rag.ingestion.pdf_profiles import (
    PROFILES,
    REVIEWED_SOURCES,
    PDFPageProfile,
    PDFProfile,
    PDFRegion,
    PDFTableSpec,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/pdf"
MANIFEST = {m["document_id"]: m for m in json.loads((FIXTURES / "manifest.json").read_text())}


def source_fixture(registry, name, monkeypatch):
    path = FIXTURES / (name + ".pdf")
    source = registry.get(name)
    source = source.model_copy(
        update={
            "access": source.access.model_copy(
                update={
                    "sha256": sha256_bytes(path.read_bytes()),
                    "bytes": path.stat().st_size,
                }
            )
        }
    )
    registry = registry.model_copy(
        update={"sources": tuple(source if s.document_id == name else s for s in registry.sources)}
    )
    monkeypatch.setitem(
        REVIEWED_SOURCES, name, {**REVIEWED_SOURCES[name], "raw_sha256": source.access.sha256}
    )
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
        document_version_id="docv_source_derived_fixture",
        retrieved_at=datetime(2026, 11, 12, tzinfo=UTC),
        raw_sha256=source.access.sha256,
        page_count=source.pdf.page_count,
        text_classification=source.pdf.text_classification,
        eligibility=SourceEligibility.model_validate(
            source.selection.model_dump(exclude={"approved_pdf_for_milestone"})
        ),
    )
    return (
        PDFExtractor(registry, name, comparison=not source.selection.default_index),
        path,
        document,
    )


@pytest.mark.parametrize("name", PROFILES)
def test_all_reviewed_source_layouts_are_portable_and_deterministic(
    reviewed_registry, monkeypatch, name
):
    extractor, path, document = source_fixture(reviewed_registry, name, monkeypatch)
    result = extractor.extract_report(path, document)
    assert result.review_state == "pending"
    assert result == extractor.extract_report(path, document)
    assert PDFExtraction.model_validate_json(result.model_dump_json()) == result
    assert extractor.extract(path, document) == result.blocks
    assert len(result.pages) == document.page_count
    assert result.document.dates == document.dates
    assert all(
        loc.physical_page and loc.bbox and loc.extraction_method == "native_pdf"
        for b in result.blocks
        for loc in b.source_locations
    )
    expected = MANIFEST[name]["expected_tables"]
    assert len(result.tables) == len(expected)
    for table, rows in zip(result.tables, expected, strict=True):
        assert [
            {"values": list(r.values), "category": r.category, "state": r.state}
            for r in table.parts[0].rows
        ] == rows
    assert (
        path.stat().st_size > 0
        and sha256_bytes(path.read_bytes()) == MANIFEST[name]["fixture_sha256"]
    )
    assert MANIFEST[name]["fixture_sha256"] != MANIFEST[name]["original_sha256"]


def text(result):
    return "\n".join(b.text for b in result.blocks)


def test_column_order_and_source_dates_survive(reviewed_registry, monkeypatch):
    extractor, path, document = source_fixture(
        reviewed_registry, "mn_cross_contamination_pdf", monkeypatch
    )
    result = extractor.extract_report(path, document)
    output = text(result)
    assert (
        output.index("Keep it clean:")
        < output.index("Cutting boards:")
        < output.index("Marinating food:")
        < output.index("When serving food")
    )
    clean = next(b for b in result.blocks if "Harmful bacteria can spread" in b.text)
    assert (
        "cutting board" not in clean.text.lower().split("onto")[0]
    )  # No adjacent right-column sentence.
    assert "Created 4/2006, modified 2025" in output
    assert result.pages[0].printed_page_label == "1"
    assert all(
        w.location.bbox[2] <= result.pages[w.location.physical_page - 1].width for w in result.words
    )


def test_temperature_table_exclusions_keep_qualifications(reviewed_registry, monkeypatch):
    extractor, path, document = source_fixture(
        reviewed_registry, "mn_safe_grilling_pdf", monkeypatch
    )
    result = extractor.extract_report(path, document)
    table = result.tables[0]
    excluded = next(r for r in table.parts[0].rows if r.state == "excluded")
    assert excluded.values[-1] == "145°F" and excluded.values[0].startswith("Fresh beef")
    block = next(b for b in result.blocks if b.block_type == "table")
    assert "Fresh beef" not in block.text
    assert "Until yolk and white are firm" in block.text
    assert (
        "Regulated facilities may cook to different temperatures"
        in block.table_context.footnotes[0]
    )
    assert len(block.source_locations) == 2  # Table and source qualification.
    layout = next(layout for layout in result.layouts if layout.block_id == block.block_id)
    excluded_ids = {
        wid
        for cell in table.parts[0].cells
        if cell.row == excluded.source_row
        for wid in cell.word_ids
    }
    assert not excluded_ids & set(layout.word_ids)
    assert "Color is not a reliable indicator of doneness." in text(result)
    assert "above 90°F" in text(result) and "more than one hour" in text(result)


def test_storage_and_fda_tables_preserve_units_empty_values_and_rest(
    reviewed_registry, monkeypatch
):
    extractor, path, document = source_fixture(
        reviewed_registry, "mn_storage_chart_pdf", monkeypatch
    )
    result = extractor.extract_report(path, document)
    table = result.tables[0]
    tv = next(r for r in table.parts[0].rows if r.values[0] == "TV dinners")
    assert tv.values == ("TV dinners", "", "6") and tv.category is None
    egg = next(r for r in table.parts[0].rows if r.values[0] == "fresh in shell")
    assert egg.category == "Eggs" and egg.values[-1] == "don’t freeze"
    block = next(b for b in result.blocks if b.block_type == "table")
    assert "Months at 0°F" in block.text and "taste loss" in block.text
    assert result.comparison_only and result.pages[0].printed_page_label is None
    assert "Refrigerator and Freezer Storage Chart" in text(result)
    extractor, path, document = source_fixture(
        reviewed_registry, "fda_safe_handling_pdf", monkeypatch
    )
    result = extractor.extract_report(path, document)
    table = result.tables[0]
    assert table.parts[0].header_origin == "reviewed_column_roles"
    beef = table.parts[0].rows[0]
    assert beef.values[-1] == "145 °F with a 3 minute rest time"
    assert "healthcare provider" not in text(result)
    assert [p.printed_page_label for p in result.pages] == ["1", "2"]


def test_hhs_calculator_supplement_and_sodium_lookup_regions_are_held(
    reviewed_registry, monkeypatch
):
    for name in [
        "hhs_adults_older_adults_pdf",
        "hhs_kids_teens_pdf",
        "nhlbi_sodium_tips_pdf",
        "dga_2025_2030_pdf",
    ]:
        extractor, path, document = source_fixture(reviewed_registry, name, monkeypatch)
        result = extractor.extract_report(path, document)
        output = text(result)
        assert "DRI" not in output and "per kilogram" not in output and "2,000" not in output
        assert "supplement" not in output.lower()
        if name == "nhlbi_sodium_tips_pdf":
            assert result.pages[1].status == "excluded"
            assert "lower blood pressure" not in output
            assert "Spice it up." in output
        if name == "dga_2025_2030_pdf":
            assert result.pages[2].printed_page_label == "2"  # Physical page 3, printed page 2.
            assert result.pages[0].printed_page_label is None
        if name == "hhs_kids_teens_pdf":
            assert "Ages 3 to 17" in output


def synthetic_pdf(tmp_path, pages=2):
    path = tmp_path / "synthetic.pdf"
    c = canvas.Canvas(str(path), pagesize=(600, 800), invariant=1)
    for p in range(pages):
        c.setFont("Helvetica", 10)
        c.drawString(30, 785, "REPEATED HEADER")
        c.drawString(30, 50, "Warning: freezer times are for quality.")
        c.drawString(285, 20, str(p + 7))
        if p == 0:
            c.drawString(35, 680, "Keep food refrigerated and")
            c.drawString(330, 680, "Separate right column guidance.")
        else:
            c.drawString(35, 680, "use within the stated time.")
        c.showPage()
    c.save()
    document = DocumentVersion(
        document_id="synthetic",
        document_version_id="docv_synthetic",
        family_id="synthetic",
        document_name="Project-created native fixture",
        publisher="Project",
        source_url="https://example.gov/fixture.pdf",
        format="pdf",
        retrieved_at=datetime(2026, 11, 12, tzinfo=UTC),
        source_tier="official_guidance",
        jurisdiction="US",
        target_audience="US",
        intended_audience="household consumers",
        content_role="consumer_guidance",
        eligibility=SourceEligibility(
            decision="approved_with_exclusions", default_index=True, eligible_sections=("Fixture",)
        ),
        rights_review={"status": "project_created", "scope": "Project-created fixture"},
        raw_sha256=sha256_bytes(path.read_bytes()),
        page_count=pages,
    )
    return path, document


def test_explicit_prose_continuation_page_mismatch_and_meaningful_footers(tmp_path):
    path, document = synthetic_pdf(tmp_path)
    profile = PDFProfile(
        tuple(
            PDFPageProfile(
                (
                    PDFRegion("left", (30, 90, 290, 180), continuation_key="prose"),
                    *((PDFRegion("right", (310, 90, 570, 180)),) if page == 0 else ()),
                    PDFRegion("warning", (30, 740, 570, 770), kind="note"),
                )
            )
            for page in range(2)
        )
    )
    result = _extract_native(path, document, profile)
    assert result.review_state == "pending"
    assert [p.printed_page_label for p in result.pages] == ["7", "8"]
    continued = result.blocks[0]
    assert continued.text == "Keep food refrigerated and use within the stated time."
    assert [loc.physical_page for loc in continued.source_locations] == [1, 2]
    assert "REPEATED HEADER" not in text(result)
    assert text(result).count("Warning: freezer times are for quality.") == 2
    assert any(d.code == "repeated_furniture_removed" for d in result.diagnostics)


def table_pdf(tmp_path, *, conflict=False, boundary_cross=False):
    path, document = synthetic_pdf(tmp_path)
    c = canvas.Canvas(str(path), pagesize=(600, 800), invariant=1)
    for p in range(2):
        c.setFont("Helvetica", 10)
        for x in [30, 200, 370]:
            c.line(x, 450, x, 650)
        for y in [450, 500, 550, 600, 650]:
            c.line(30, y, 370, y)
        c.drawString(35, 625, "Food")
        c.drawString(205, 625, "Changed" if conflict and p else "Days at 40 F")
        c.drawString(35, 575, "Soup" if not p else "Meat")
        c.drawString(190 if boundary_cross else 205, 575, "3 to 4")
        c.drawString(35, 525, "Eggs")
        c.drawString(205, 525, "21")
        c.drawString(35, 475, "TV dinners")  # Deliberately empty value.
        c.drawString(285, 20, str(p + 3))
        c.showPage()
    c.save()
    document = document.model_copy(update={"raw_sha256": sha256_bytes(path.read_bytes())})
    spec = PDFTableSpec(
        "storage",
        (30, 200, 370),
        (150, 200, 250, 300, 350),
        ("Food", "Days at 40 F"),
        continuation_key="storage",
    )
    profile = PDFProfile((PDFPageProfile((), (spec,)), PDFPageProfile((), (spec,))))
    return path, document, profile


def test_continued_tables_preserve_original_pages_and_repeat_headers(tmp_path):
    path, document, profile = table_pdf(tmp_path)
    result = _extract_native(path, document, profile)
    assert len(result.tables) == 1 and len(result.tables[0].parts) == 2
    assert result.tables[0].parts[1].continuation_of_page == 1
    block = result.blocks[0]
    assert block.block_type == "table" and [
        loc.physical_page for loc in block.source_locations
    ] == [
        1,
        2,
    ]
    assert "Food: Soup | Days at 40 F: 3 to 4" in block.text
    assert "Food: Meat | Days at 40 F: 3 to 4" in block.text
    assert result.tables[0].parts[0].rows[-1].values[-1] == ""


@pytest.mark.parametrize("option", ["conflict", "boundary_cross"])
def test_ambiguous_headers_and_cells_quarantine_the_entire_continued_table(tmp_path, option):
    path, document, profile = table_pdf(tmp_path, **{option: True})
    result = _extract_native(path, document, profile)
    assert result.review_state == "quarantined"
    assert result.tables[0].state == "quarantined" and result.tables[0].block_id is None
    assert not result.blocks
    assert any(d.code.startswith("ambiguous_table") for d in result.diagnostics)


def test_missing_table_detection_is_never_silently_flattened(tmp_path, monkeypatch):
    import pdfplumber.page

    path, document, profile = table_pdf(tmp_path)
    monkeypatch.setattr(pdfplumber.page.CroppedPage, "find_tables", lambda *a, **k: [])
    with pytest.raises(PDFExtractionError, match="expected guidance table"):
        _extract_native(path, document, profile)


def test_unknown_tables_fail_and_absent_native_regions_quarantine(tmp_path):
    path, document, profile = table_pdf(tmp_path)
    prose = PDFProfile(
        (PDFPageProfile((PDFRegion("body", (30, 100, 550, 400)),)),) * 2,
        strict_unprofiled_tables=True,
    )
    with pytest.raises(PDFExtractionError, match="unreviewed visible table"):
        _extract_native(path, document, prose)
    path, document = synthetic_pdf(tmp_path)
    missing = PDFProfile((PDFPageProfile((PDFRegion("missing", (30, 400, 500, 500)),)),) * 2)
    result = _extract_native(path, document, missing)
    assert result.review_state == "quarantined" and not result.blocks


def test_corruption_encryption_count_and_registry_drift_fail(
    tmp_path, reviewed_registry, monkeypatch
):
    extractor, path, document = source_fixture(
        reviewed_registry, "mn_cross_contamination_pdf", monkeypatch
    )
    copy = tmp_path / "copy.pdf"
    copy.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(PDFExtractionError, match="SHA-256"):
        extractor.extract_report(copy, document)
    with pytest.raises(PDFExtractionError, match="metadata"):
        extractor.extract_report(path, document.model_copy(update={"publisher": "Wrong publisher"}))
    with pytest.raises(PDFExtractionError, match="page count"):
        _extract_native(path, document.model_copy(update={"page_count": 2}), extractor.profile)
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    writer.append(PdfReader(path))
    writer.encrypt("secret")
    writer.write(copy)
    with pytest.raises(PDFExtractionError, match="encrypted"):
        _extract_native(
            copy,
            document.model_copy(update={"raw_sha256": sha256_bytes(copy.read_bytes())}),
            extractor.profile,
        )
    with pytest.raises(PDFExtractionError, match="approved"):
        PDFExtractor(reviewed_registry, "nhlbi_dash_full_pdf")
    with pytest.raises(PDFExtractionError, match="approved"):
        PDFExtractor(reviewed_registry, "mn_storage_chart_pdf")


def test_native_contracts_reject_missing_word_relationships(reviewed_registry, monkeypatch):
    extractor, path, document = source_fixture(
        reviewed_registry, "mn_cross_contamination_pdf", monkeypatch
    )
    result = extractor.extract_report(path, document)
    payload = result.model_dump()
    payload["layouts"][0]["word_ids"] = ("pdf_word_missing",)
    with pytest.raises(ValidationError, match="missing native words"):
        PDFExtraction.model_validate(payload)


def test_no_native_table_headers_quarantine_without_inventing_values(tmp_path):
    path, document = synthetic_pdf(tmp_path)
    table = PDFTableSpec("empty", (30, 200, 370), (400, 450, 500), ("Food", "Days"))
    profile = PDFProfile((PDFPageProfile((), (table,)),) * 2)
    result = _extract_native(path, document, profile)
    assert result.review_state == "quarantined" and not result.blocks
    assert all(t.parts[0].header_origin == "unresolved_native_headers" for t in result.tables)
    assert all(
        c.text == "" and c.status == "empty" for t in result.tables for c in t.parts[0].cells
    )


def test_pdf_page_labels_and_rotated_page_geometry_remain_separate(tmp_path):
    from pypdf import PdfReader, PdfWriter
    from pypdf.constants import PageLabelStyle

    path, document = synthetic_pdf(tmp_path)
    writer = PdfWriter()
    writer.append(PdfReader(path))
    writer.set_page_label(0, 1, style=PageLabelStyle.DECIMAL, start=20)
    changed = tmp_path / "labeled.pdf"
    writer.write(changed)
    profile = PDFProfile((PDFPageProfile((PDFRegion("all", (0, 0, 600, 800)),)),) * 2)
    result = _extract_native(
        changed,
        document.model_copy(update={"raw_sha256": sha256_bytes(changed.read_bytes())}),
        profile,
    )
    assert [p.pdf_page_label for p in result.pages] == ["20", "21"]
    assert [p.printed_page_label for p in result.pages] == ["7", "8"]
    writer.pages[0].rotate(90)
    writer.write(changed)
    profile = replace(
        profile, pages=(PDFPageProfile((PDFRegion("all", (0, 0, 800, 600)),)), profile.pages[1])
    )
    result = _extract_native(
        changed,
        document.model_copy(update={"raw_sha256": sha256_bytes(changed.read_bytes())}),
        profile,
    )
    assert result.pages[0].rotation == 90 and result.pages[0].width == 800
    assert all(
        w.location.bbox[2] <= result.pages[w.location.physical_page - 1].width for w in result.words
    )
