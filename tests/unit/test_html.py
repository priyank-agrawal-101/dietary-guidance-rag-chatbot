"""Source-derived fixtures and clearly synthetic HTML edge cases; no network/staging input."""

import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from dietary_rag.corpus.contracts import DocumentVersion, SourceEligibility
from dietary_rag.corpus.identity import sha256_bytes
from dietary_rag.ingestion.html import HTMLExtractionError, HTMLExtractor, _table_grid
from dietary_rag.ingestion.html_contracts import HTMLExtraction
from dietary_rag.ingestion.html_profiles import PROFILES

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/html"


def extract_fixture(registry, tmp_path, name, content=None):
    content = content if content is not None else (FIXTURES / f"{name}.html").read_bytes()
    source = registry.get(name)
    source = source.model_copy(
        update={
            "access": source.access.model_copy(
                update={
                    "sha256": sha256_bytes(content),
                    "bytes": len(content),
                }
            )
        }
    )
    registry = registry.model_copy(
        update={"sources": tuple(source if s.document_id == name else s for s in registry.sources)}
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
        document_version_id="docv_synthetic_fixture",
        retrieved_at=source.access.checked_at,
        raw_sha256=source.access.sha256,
        eligibility=SourceEligibility.model_validate(
            source.selection.model_dump(exclude={"approved_pdf_for_milestone"})
        ),
    )
    raw = tmp_path / "fixture.html"
    raw.write_bytes(content)
    extractor = HTMLExtractor(registry, name)
    return extractor, raw, document


def run_fixture(registry, tmp_path, name, content=None):
    extractor, raw, document = extract_fixture(registry, tmp_path, name, content)
    return extractor.extract_report(raw, document)


@pytest.mark.parametrize("name", PROFILES)
def test_selected_source_profiles_portable_and_deterministic(reviewed_registry, tmp_path, name):
    extractor, raw, document = extract_fixture(reviewed_registry, tmp_path, name)
    result = extractor.extract_report(raw, document)
    assert result == extractor.extract_report(raw, document)
    assert HTMLExtraction.model_validate_json(result.model_dump_json()) == result
    assert extractor.extract(raw, document) == result.blocks
    assert len(result.tables) == PROFILES[name].expected_tables
    assert [b.sequence_index for b in result.blocks] == list(range(len(result.blocks)))
    assert len({b.block_id for b in result.blocks}) == len(result.blocks)
    assert all(b.source_locations[0].dom_path for b in result.blocks)
    assert all(b.source_locations[0].review_state == "pending" for b in result.blocks)
    assert result.document.dates == document.dates
    assert result.review_state == "pending"


def test_fixture_manifest_does_not_pretend_to_be_an_acquisition():
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    assert {m["document_id"] for m in manifest} == set(PROFILES)
    for m in manifest:
        assert (
            sha256_bytes((FIXTURES / f"{m['document_id']}.html").read_bytes())
            == m["fixture_sha256"]
        )
        assert m["fixture_sha256"] != m["reviewed_raw_sha256"]


def text(result):
    return "\n".join(b.text for b in result.blocks)


def test_minnesota_nested_warning_conditions_and_anchors(reviewed_registry, tmp_path):
    result = run_fixture(reviewed_registry, tmp_path, "mn_cold_storage")
    assert "Freezer burn is a food-quality issue, not a food safety issue." in text(result)
    assert "  - It appears" in text(result)
    freeze = next(
        b
        for b in result.blocks
        if b.block_type == "recommendation_list" and b.heading_path[-1] == "Freeze"
    )
    assert freeze.source_locations[0].source_anchor == "free"
    assert "On this page:" not in text(result)
    result = run_fixture(reviewed_registry, tmp_path, "mn_safe_temperatures")
    assert "no longer pink" not in text(result)
    assert "Ground beef should be cooked to an internal temperature of 160°F." in text(result)
    assert "Reduce that to one hour when the temperature is above 90° F." in text(result)


def test_storage_chart_all_cells_headers_categories_and_qualifications(reviewed_registry, tmp_path):
    result = run_fixture(reviewed_registry, tmp_path, "mn_storage_chart")
    table = result.tables[0]
    original = BeautifulSoup(table.original_html, "html.parser").table
    original_cells = original.find_all(["td", "th"])
    assert len(table.cells) == len(original_cells)
    for cell, raw in zip(table.cells, original_cells, strict=True):
        assert cell.text == " ".join(raw.stripped_strings)
        for r in range(cell.row, cell.row + cell.rowspan):
            for c in range(cell.column, cell.column + cell.colspan):
                assert table.cells[table.grid[r][c]] == cell
    assert len(table.rows) == 28
    egg = next(r for r in table.rows if r.values[1] == "fresh in shell")
    assert egg.category == "Eggs"
    assert egg.values == ("", "fresh in shell", "21", "don’t freeze")
    assert egg.column_headers == (
        "Product",
        "Product",
        "Refrigerator (Days at 40°F)",
        "Freezer (Months at 0°F)",
    )
    assert next(r for r in table.rows if r.values[0] == "Mayonnaise").category is None
    tv = next(r for r in table.rows if r.values[0] == "TV dinners")
    assert tv.values[-2] == ""  # Never invent the missing refrigerator time.
    block = next(b for b in result.blocks if b.block_type == "table")
    assert "(Days at 40°F)" in block.table_context.units
    assert "taste loss" in " ".join(block.table_context.footnotes)
    assert "Revised April, 2007" in text(result)


def test_cdc_category_headers_and_safety_conditions(reviewed_registry, tmp_path):
    result = run_fixture(reviewed_registry, tmp_path, "cdc_pregnancy_food_safety")
    table = result.tables[0]
    assert len(table.rows) == 9
    assert all(r.column_headers == ("Riskier Choice", "Safer Choice") for r in table.rows)
    assert table.rows[0].category == "Poultry and Meat"
    assert "rest for 3 minutes" in table.rows[0].values[1]
    assert (
        "1 hour if"
        in next(r for r in table.rows if r.category == "Vegetables and Fruits").values[0]
    )
    assert (
        "when made with pasteurized milk"
        in next(r for r in table.rows if r.category == "Cheese").values[1]
    )
    assert "mercury" not in text(result)
    assert "Shark" not in text(result) and "Pollock" not in text(result)
    assert "Why it's important" not in text(result)
    assert (
        "mercury" in table.original_html
    )  # Original structure is a review artifact, not canonical evidence.


def test_medical_calorie_and_process_exclusions_keep_legitimate_warnings(
    reviewed_registry, tmp_path
):
    label = text(run_fixture(reviewed_registry, tmp_path, "fda_nutrition_label_current"))
    assert "It is not a recommendation of how much you should eat or drink." in label
    assert "5% DV or less" in label and "20% DV or more" in label
    assert "2,000" not in label and "280 calories" not in label
    assert "Dietary Trade-Offs" not in label and "cardiovascular disease" not in label
    assert "How the Daily Values Relate" not in label
    seafood = run_fixture(reviewed_registry, tmp_path, "fda_seafood")
    assert "Most seafood should be cooked to an internal temperature of 145°F." in text(seafood)
    assert "If you don’t have a food thermometer" not in text(seafood)
    assert "flesh is clear" not in text(seafood)
    assert "Discard Cracked/Broken Ones" in text(
        seafood
    )  # Buying guidance must survive cooking-only exclusions.
    assert "freezing doesn't kill all harmful germs" in text(seafood)
    assert "Special Health Notes" not in text(seafood)
    assert text(seafood).count("Follow these serving guidelines once") == 1
    emergency = run_fixture(reviewed_registry, tmp_path, "cdc_emergency_food_safety")
    assert "bleach" not in text(emergency) and "Re-label" not in text(emergency)
    assert "Never taste food" in text(emergency)
    assert "When in doubt, throw it out!" in text(emergency)
    assert "48 hours in a full freezer" in text(emergency)


def synthetic(body):
    return (
        '<html><head><meta charset="utf-8"></head><body><nav>OUTSIDE NAV</nav>'
        '<main><div class="contentLinks"><div class="field--name-body"><div class="field__item">'
        "<h1>Project-created fixture</h1>" + body + "</div></div></div></main></body></html>"
    ).encode()


def test_noise_responsive_duplicates_and_order(reviewed_registry, tmp_path):
    result = run_fixture(
        reviewed_registry,
        tmp_path,
        "mn_cold_storage",
        synthetic("""
      <nav>INNER NAV</nav><script>BAD SCRIPT</script><aside>UNRELATED CARD</aside>
      <div class="advertisement">BUY THIS</div><p hidden>HIDDEN</p>
      <div data-responsive-copy="desktop"><p>Wash hands.</p></div>
      <div data-responsive-copy="mobile"><p>Wash hands.</p></div>
      <h2 id="safe">Safety</h2><p>Keep food cold.</p>
      <ol><li>First action<ul><li>Keep the qualification.</li></ul></li><li>Second action</li></ol>
      <p>Keep food cold.</p>"""),
    )
    output = text(result)
    for marker in ["NAV", "BAD SCRIPT", "UNRELATED CARD", "BUY THIS", "HIDDEN"]:
        assert marker not in output
    assert output.count("Wash hands.") == 1
    assert output.count("Keep food cold.") == 2  # Ordinary repeated guidance stays.
    assert "1. First action\n  - Keep the qualification.\n2. Second action" in output
    assert any(d.code == "responsive_duplicate_removed" for d in result.diagnostics)
    assert result.blocks[-1].source_locations[0].source_anchor == "safe"


def test_missing_profile_root_changed_bytes_and_selection_fail(reviewed_registry, tmp_path):
    extractor, raw, document = extract_fixture(reviewed_registry, tmp_path, "mn_cold_storage")
    raw.write_bytes(raw.read_bytes() + b"changed")
    with pytest.raises(HTMLExtractionError, match="SHA-256"):
        extractor.extract(raw, document)
    with pytest.raises(HTMLExtractionError, match="main-content root"):
        run_fixture(reviewed_registry, tmp_path, "mn_cold_storage", b"<html><h1>Shell</h1></html>")
    source = reviewed_registry.get("mn_cold_storage")
    changed = source.model_copy(
        update={
            "selection": source.selection.model_copy(update={"excluded_sections": ("New section",)})
        }
    )
    registry = reviewed_registry.model_copy(
        update={
            "sources": tuple(
                changed if s.document_id == source.document_id else s
                for s in reviewed_registry.sources
            )
        }
    )
    with pytest.raises(HTMLExtractionError, match="selection changed"):
        HTMLExtractor(registry, source.document_id)
    extractor, raw, document = extract_fixture(reviewed_registry, tmp_path, "mn_cold_storage")
    with pytest.raises(HTMLExtractionError, match="metadata"):
        extractor.extract_report(raw, document.model_copy(update={"publisher": "Wrong publisher"}))


@pytest.mark.parametrize("name", ["fda_pregnancy_food_safety", "wa_power_outages_current"])
def test_discovery_and_reference_sources_are_rejected(reviewed_registry, name):
    with pytest.raises(HTMLExtractionError, match="not approved consumer guidance"):
        HTMLExtractor(reviewed_registry, name)


@pytest.mark.parametrize(
    "markup",
    [
        '<table><tr><td rowspan="0">X</td></tr></table>',
        '<table><tr><td colspan="huge">X</td></tr></table>',
        "<table><tr><td>X</td><td>Y</td></tr><tr><td>Z</td></tr></table>",
        "<table><tr><td><table><tr><td>X</td></tr></table></td></tr></table>",
        '<table><tr><td>A</td><td rowspan="2">B</td></tr><tr><td colspan="2">C</td></tr></table>',
    ],
)
def test_ambiguous_tables_fail_instead_of_flattening(markup):
    table = BeautifulSoup(markup, "html.parser").table
    with pytest.raises(HTMLExtractionError):
        _table_grid(table, {id(n): n.name for n in table.find_all(True)})


def test_spans_headers_empty_cells_and_linked_footnote(reviewed_registry, tmp_path):
    content = (FIXTURES / "mn_storage_chart.html").read_text()
    soup = BeautifulSoup(content, "html.parser")
    table = soup.find_all("table")[1]
    replacement = BeautifulSoup(
        """<table id="storage"><caption>Storage fixture</caption>
      <thead><tr><th rowspan="2">Food</th><th colspan="2">Storage</th></tr>
      <tr><th>Refrigerator (Days at 40°F)</th><th>Freezer (Months at 0°F)</th></tr></thead>
      <tbody><tr><th scope="row">Soup<a href="#fn">*</a></th><td>3</td><td></td></tr>
      <tr><th scope="row" rowspan="2">Meat</th><td>2</td><td>4</td></tr>
      <tr><td>1</td><td>3</td></tr></tbody></table><p id="fn">* Freezer times are for quality.</p>""",
        "html.parser",
    )
    table.replace_with(replacement.table)
    soup.select_one("#storage").insert_after(replacement.p)
    result = run_fixture(reviewed_registry, tmp_path, "mn_storage_chart", str(soup).encode())
    native = result.tables[0]
    assert native.rows[0].column_headers == (
        "Food",
        "Storage / Refrigerator (Days at 40°F)",
        "Storage / Freezer (Months at 0°F)",
    )
    assert native.rows[0].values[-1] == ""
    assert native.rows[1].values[0] == native.rows[2].values[0] == "Meat"
    context = next(b.table_context for b in result.blocks if b.block_type == "table")
    assert "* Freezer times are for quality." in context.footnotes


def test_expected_visible_table_cannot_silently_disappear(reviewed_registry, tmp_path):
    soup = BeautifulSoup((FIXTURES / "mn_storage_chart.html").read_text(), "html.parser")
    soup.find_all("table")[1].decompose()
    with pytest.raises(HTMLExtractionError, match="expected 1 guidance tables"):
        run_fixture(reviewed_registry, tmp_path, "mn_storage_chart", str(soup).encode())


def test_multiple_roots_and_changed_section_layout_fail(reviewed_registry, tmp_path):
    content = synthetic("<p>Keep food safe.</p>")
    soup = BeautifulSoup(content, "html.parser")
    from copy import deepcopy

    soup.main.append(deepcopy(soup.select_one(".contentLinks")))
    with pytest.raises(HTMLExtractionError, match="found 2"):
        run_fixture(reviewed_registry, tmp_path, "mn_cold_storage", str(soup).encode())
    soup = BeautifulSoup((FIXTURES / "fda_produce.html").read_text(), "html.parser")
    soup.find("h2", string="Buy Right").string.replace_with("New unreviewed section")
    with pytest.raises(HTMLExtractionError, match="reviewed sections missing"):
        run_fixture(reviewed_registry, tmp_path, "fda_produce", str(soup).encode())


def test_cdc_warning_callouts_and_heading_context_survive(reviewed_registry, tmp_path):
    result = run_fixture(reviewed_registry, tmp_path, "cdc_food_safety")
    warning = next(b for b in result.blocks if "Washing these foods can spread germs" in b.text)
    assert warning.heading_path[-1] == "Separate foods to avoid cross-contamination"
    assert warning.source_locations[0].source_anchor
    assert "Always wash hands after handling uncooked meat" in text(result)


def test_invalid_native_report_relationships_are_rejected(reviewed_registry, tmp_path):
    from pydantic import ValidationError

    result = run_fixture(reviewed_registry, tmp_path, "mn_storage_chart")
    payload = result.model_dump()
    payload["tables"][0]["grid"] = ((9999,),)
    with pytest.raises(ValidationError, match="grid index"):
        HTMLExtraction.model_validate(payload)
    payload = result.model_dump()
    payload["blocks"][0]["document_version_id"] = "docv_wrong"
    with pytest.raises(ValidationError, match="resolve to this document"):
        HTMLExtraction.model_validate(payload)
