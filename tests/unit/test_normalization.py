"""Portable native fixtures, exact table fidelity and scoped exclusion regressions."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from dietary_rag.corpus.contracts import DocumentVersion, SourceEligibility
from dietary_rag.corpus.identity import sha256_bytes, stable_id
from dietary_rag.ingestion.html import HTMLExtractor
from dietary_rag.ingestion.html_profiles import PROFILES as HTML_PROFILES
from dietary_rag.ingestion.normalization import (
    NormalizationError,
    Normalizer,
    clean_text,
    protected_facts,
)
from dietary_rag.ingestion.normalization_contracts import NormalizationReview, NormalizedExtraction
from dietary_rag.ingestion.pdf import PDFExtractor
from dietary_rag.ingestion.pdf_profiles import PROFILES as PDF_PROFILES
from dietary_rag.ingestion.pdf_profiles import REVIEWED_SOURCES

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def native_fixture(registry, name, monkeypatch):
    source = registry.get(name)
    kind = source.format
    path = FIXTURES / kind / f"{name}.{kind}"
    content = path.read_bytes()
    source = source.model_copy(
        update={
            "access": source.access.model_copy(
                update={"sha256": sha256_bytes(content), "bytes": len(content)}
            )
        }
    )
    registry = registry.model_copy(
        update={"sources": tuple(source if s.document_id == name else s for s in registry.sources)}
    )
    if kind == "pdf":
        monkeypatch.setitem(
            REVIEWED_SOURCES, name, {**REVIEWED_SOURCES[name], "raw_sha256": source.access.sha256}
        )
    fields = {
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
    document = DocumentVersion(
        **source.model_dump(include=fields),
        document_version_id="docv_normalization_fixture",
        retrieved_at=source.access.checked_at,
        raw_sha256=source.access.sha256,
        eligibility=SourceEligibility.model_validate(
            source.selection.model_dump(exclude={"approved_pdf_for_milestone"})
        ),
        **(
            {
                "page_count": source.pdf.page_count,
                "text_classification": source.pdf.text_classification,
            }
            if kind == "pdf"
            else {}
        ),
    )
    parser = (
        HTMLExtractor(registry, name)
        if kind == "html"
        else PDFExtractor(registry, name, comparison=not source.selection.default_index)
    )
    return Normalizer(registry), parser.extract_report(path, document)


def resign(extraction):
    payload = extraction.model_dump(mode="python", exclude={"extraction_id"})
    kind = "html_extraction" if extraction.document.format == "html" else "pdf_extraction"
    if kind == "html_extraction":
        payload.pop("review_state")
    return extraction.model_copy(update={"extraction_id": stable_id(kind, payload)})


@pytest.mark.parametrize("name", [*HTML_PROFILES, *PDF_PROFILES])
def test_all_selected_and_comparison_layouts_normalize_without_value_or_location_drift(
    reviewed_registry, monkeypatch, name
):
    normalizer, native = native_fixture(reviewed_registry, name, monkeypatch)
    result = normalizer.normalize(native)
    assert result.review_state == "pending"
    assert len(result.blocks) == len(native.blocks)
    assert result == normalizer.normalize(native)
    assert NormalizedExtraction.model_validate_json(result.model_dump_json()) == result
    for decision, original, canonical in zip(
        result.decisions, native.blocks, result.blocks, strict=True
    ):
        assert decision.original_block_id == original.block_id
        assert original.source_locations == canonical.source_locations
        assert protected_facts(original.text) == protected_facts(canonical.text)
    for original, table in zip(native.tables, result.tables, strict=True):
        rows = (
            original.rows
            if native.document.format == "html"
            else [r for p in original.parts for r in p.rows if r.state == "retained"]
        )
        assert [r.values for r in table.rows] == [r.values for r in rows]
        assert [r.category for r in table.rows] == [r.category for r in rows]
    assert result.document == native.document


@pytest.mark.parametrize(
    "text,expected",
    [
        ("  Do\u00a0not\tfreeze.  ", "Do not freeze."),
        (
            "40°F; 1½ cups; 2–3 days; 145 °F with a 3-minute rest.",
            "40°F; 1½ cups; 2–3 days; 145 °F with a 3-minute rest.",
        ),
        ("Wash\n hands, not raw poultry.", "Wash hands, not raw poultry."),
        ("vege\u00adtables and ﬁsh", "vegetables and fish"),
        (
            "cross-\ncontamination and low- or no-salt-added",
            "cross- contamination and low- or no-salt-added",
        ),
        ("Footnote [1]: quality, not safety.", "Footnote [1]: quality, not safety."),
    ],
)
def test_unicode_and_whitespace_are_cautious(text, expected):
    assert clean_text(text) == expected
    assert protected_facts(text) == protected_facts(expected)


def test_nested_list_depth_and_footnote_lines_survive():
    text = "- Do not freeze.\n  - This affects quality, not safety.\n    - See note [1]."
    assert clean_text(text, multiline=True, keep_indent=True) == text


def test_nhlbi_cards_and_reviewed_line_wraps_have_no_adjacent_column_leak(
    reviewed_registry, monkeypatch
):
    normalizer, native = native_fixture(reviewed_registry, "nhlbi_sodium_tips_pdf", monkeypatch)
    result = normalizer.normalize(native)
    assert (
        result.blocks[-3].text
        == "Make special requests. Ask that your meal be prepared without added salt, MSG, or salty ingredients such as bacon, pickles, olives, and cheese."
    )
    assert (
        result.blocks[-2].text
        == "Easy does it on the condiments. A little goes a long way for mustard, ketchup, horseradish, pickles, and sauces with salt-containing ingredients."
    )
    assert (
        result.blocks[-1].text
        == "Go for healthy appetizers and side dishes. Choose fruit or vegetables instead of salty snacks, chips, or fries."
    )
    assert "vegetables" in result.blocks[1].text and "low- or" in result.blocks[3].text
    assert "barbecue" in result.blocks[4].text and "sparingly" in result.blocks[4].text
    assert all(t.word_ids for t in result.transformations if t.rule == "reviewed_source_line_wrap")
    assert not any(b.source_locations[0].physical_page == 2 for b in result.blocks)


@pytest.mark.parametrize(
    "text,rule",
    [
        ("Use the DRI calculator to calculate your personal intake.", "personalized_calculator"),
        ("Your calorie target is 2,000 calories a day.", "calorie_weight_targets"),
        ("Take supplements at a dose of 400 IU.", "supplement_dosing"),
        ("Treat diabetes with this diet.", "medical_treatment"),
        ("One banana contains 422 mg potassium.", "individual_food_nutrient_lookup"),
    ],
)
def test_known_exclusions_remove_whole_paragraphs_with_original_reasons(
    reviewed_registry, monkeypatch, text, rule
):
    normalizer, native = native_fixture(reviewed_registry, "mn_cold_storage", monkeypatch)
    b = native.blocks[1].model_copy(update={"text": text})
    native = resign(native.model_copy(update={"blocks": (native.blocks[0], b, *native.blocks[2:])}))
    result = normalizer.normalize(native)
    decision = result.decisions[1]
    assert (
        decision.state == "excluded"
        and rule in decision.rules
        and decision.normalized_block_id is None
    )
    assert all(text != b.text for b in result.blocks)
    assert any(
        e.subject_id == b.block_id and e.locations == b.source_locations for e in result.exclusions
    )


def test_conditions_and_general_age_nutrient_context_are_retained(reviewed_registry, monkeypatch):
    for name in ["mn_safe_grilling_pdf", "hhs_kids_teens_pdf", "hhs_adults_older_adults_pdf"]:
        normalizer, native = native_fixture(reviewed_registry, name, monkeypatch)
        result = normalizer.normalize(native)
        text = " ".join(b.text for b in result.blocks)
        if name == "mn_safe_grilling_pdf":
            assert "Color is not a reliable indicator" in text
        if name == "hhs_kids_teens_pdf":
            assert "kids age 10 and under" in text and "energy needs" in text
        if name == "hhs_adults_older_adults_pdf":
            assert "supports healthy blood pressure" in text
        assert "supplement" not in text.lower() and "DRI Calculator" not in text


def test_tampered_native_identity_and_adjacent_words_cannot_pass(reviewed_registry, monkeypatch):
    normalizer, native = native_fixture(reviewed_registry, "nhlbi_sodium_tips_pdf", monkeypatch)
    b = native.blocks[1].model_copy(update={"text": native.blocks[1].text + " changed"})
    forged = native.model_copy(update={"blocks": (native.blocks[0], b, *native.blocks[2:])})
    with pytest.raises(NormalizationError, match="identity"):
        normalizer.normalize(forged)
    # Even a re-identified malformed adapter result cannot move adjacent words into this block.
    layout = native.layouts[1]
    other = native.layouts[-1]
    layout = layout.model_copy(update={"word_ids": layout.word_ids + (other.word_ids[0],)})
    forged = resign(
        native.model_copy(update={"layouts": (native.layouts[0], layout, *native.layouts[2:])})
    )
    result = normalizer.normalize(forged)
    assert result.review_state == "quarantined"
    assert "adjacent_column_or_excluded_region_leak" in result.decisions[1].rules


def test_table_values_and_excluded_rows_cannot_be_reintroduced(reviewed_registry, monkeypatch):
    normalizer, native = native_fixture(reviewed_registry, "mn_safe_grilling_pdf", monkeypatch)
    index = next(i for i, b in enumerate(native.blocks) if b.block_type == "table")
    b = native.blocks[index].model_copy(
        update={
            "text": native.blocks[index].text
            + "\nFood: Fresh beef, veal, pork | Safe Internal Temperature: 145°F"
        }
    )
    forged = resign(
        native.model_copy(
            update={"blocks": tuple(b if i == index else x for i, x in enumerate(native.blocks))}
        )
    )
    result = normalizer.normalize(forged)
    assert result.review_state == "quarantined" and not result.tables
    assert "table_text_cell_or_qualifier_mismatch" in result.decisions[index].rules


def test_exact_review_receipts_are_required_and_do_not_change_evidence_identity(
    reviewed_registry, monkeypatch
):
    normalizer, native = native_fixture(reviewed_registry, "mn_cold_storage", monkeypatch)
    pending = normalizer.normalize(native)
    receipt = NormalizationReview(
        normalization_id=pending.normalization_id,
        configuration_id=pending.configuration_id,
        original_extraction_id=native.extraction_id,
        raw_sha256=native.document.raw_sha256,
        block_ids=tuple(b.block_id for b in pending.blocks),
        reviewer="Project fixture review",
        scope="Transformation/exclusion fidelity only",
        note="Exact fixture comparison; no activation.",
    )
    reviewed = normalizer.normalize(native, review=receipt)
    assert reviewed.normalization_id == pending.normalization_id
    assert reviewed.review_state == "reviewed" and all(
        loc.review_state == "reviewed" for b in reviewed.blocks for loc in b.source_locations
    )
    with pytest.raises(ValidationError, match="receipt"):
        normalizer.normalize(native, review=receipt.model_copy(update={"block_ids": ()}))
    data = pending.model_dump(mode="json")
    data["blocks"][0]["source_locations"][0]["dom_path"] = "/wrong"
    with pytest.raises(ValidationError, match="original locations"):
        NormalizedExtraction.model_validate(data)


def test_ocr_uncertain_candidates_stay_out_of_normalized_blocks(reviewed_registry, tmp_path):
    from dietary_rag.ingestion.ocr import OCRExtractor
    from dietary_rag.ingestion.ocr_contracts import OCRPlan

    class Engine:
        metadata = {"name": "test_engine", "version": "1", "language": "eng"}

        def recognize(self, path, plan):
            uncertain = "page-008" in path.name
            return [
                {
                    "text": "40" if uncertain else "Clean",
                    "conf": "91" if uncertain else "99",
                    "left": "20",
                    "top": "20",
                    "width": "80",
                    "height": "30",
                    "block_num": "1",
                    "par_num": "1",
                    "line_num": "1",
                }
            ]

    doc = DocumentVersion.model_validate_json((FIXTURES / "ocr/document.json").read_text())
    plan = OCRPlan.model_validate_json((FIXTURES / "ocr/plan.json").read_text())
    native = OCRExtractor(plan, engine=Engine()).extract_report(
        FIXTURES / "ocr/project-mixed-scan.pdf", doc, output_dir=tmp_path
    )
    result = Normalizer(reviewed_registry).normalize(native)
    assert result.review_state == "quarantined" and len(result.blocks) == 4
    assert result.fixture_kind == "project_created_scan"
    assert not any(b.source_locations[0].physical_page == 8 for b in result.blocks)
    assert any(
        e.origin == "ocr_quality" and e.rule == "ocr_quality_gate" for e in result.exclusions
    )


def test_fda_label_paragraph_allowlist_blocks_worked_lookup_leak(reviewed_registry, monkeypatch):
    normalizer, native = native_fixture(
        reviewed_registry, "fda_nutrition_label_current", monkeypatch
    )
    index = next(i for i, b in enumerate(native.blocks) if b.block_type == "paragraph")
    original = native.blocks[index]
    block = original.model_copy(update={"text": "Unreviewed worked nutrient lookup example."})
    forged = resign(
        native.model_copy(
            update={
                "blocks": tuple(block if i == index else b for i, b in enumerate(native.blocks))
            }
        )
    )
    result = normalizer.normalize(forged)
    assert result.decisions[index].state == "excluded"
    assert "outside_reviewed_paragraph_allowlist" in result.decisions[index].rules


def test_invisible_text_cannot_receive_a_review_receipt(reviewed_registry, monkeypatch):
    normalizer, native = native_fixture(reviewed_registry, "mn_cold_storage", monkeypatch)
    block = native.blocks[1].model_copy(update={"text": native.blocks[1].text + "\u200b"})
    native = resign(
        native.model_copy(update={"blocks": (native.blocks[0], block, *native.blocks[2:])})
    )
    result = normalizer.normalize(native)
    assert result.review_state == "quarantined"
    assert "ambiguous_invisible_or_directional_text" in result.decisions[1].rules
