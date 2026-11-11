import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from dietary_rag.corpus.contracts import (
    AcquisitionRecord,
    ContentBlock,
    DocumentVersion,
    SourceDates,
    SourceEligibility,
    SourceLocation,
)

NOW = datetime(2026, 11, 11, tzinfo=UTC)


def test_checked_in_example_contracts_and_relationships():
    root = Path(__file__).resolve().parents[2]
    examples = json.loads((root / "docs/examples/phase0-contracts.json").read_text())
    acquisition = AcquisitionRecord.model_validate(examples["acquisition"])
    document = DocumentVersion.model_validate(examples["document"])
    block = ContentBlock.model_validate(examples["block"])
    assert acquisition.document_id == document.document_id
    assert acquisition.raw_sha256 == document.raw_sha256
    assert block.document_version_id == document.document_version_id
    assert block.source_locations[0].physical_page <= document.page_count
    assert document.dates.publication_year is None


def document_payload():
    return {
        "document_id": "example",
        "document_version_id": "docv_example",
        "family_id": "example",
        "document_name": "Example guidance",
        "publisher": "Example US authority",
        "source_url": "https://example.gov/guidance.pdf",
        "format": "pdf",
        "retrieved_at": NOW,
        "source_tier": "official_guidance",
        "jurisdiction": "US",
        "target_audience": "US",
        "intended_audience": "household consumers",
        "content_role": "consumer_guidance",
        "eligibility": {
            "decision": "approved_with_exclusions",
            "default_index": True,
            "eligible_sections": ["General guidance"],
        },
        "rights_review": {"status": "reviewed", "scope": "Educational text only"},
        "raw_sha256": "a" * 64,
        "page_count": 10,
        "dates": {"edition": "2025–2030", "source_stated_dates": ["Modified 2026"]},
    }


def test_unknown_publication_year_and_edition_survive_json_roundtrip():
    document = DocumentVersion.model_validate(document_payload())
    restored = DocumentVersion.model_validate_json(document.model_dump_json())
    assert restored == document
    assert restored.dates.publication_year is None
    assert restored.dates.edition == "2025–2030"
    assert SourceDates().publication_year is None


@pytest.mark.parametrize(
    "change",
    [
        {"publisher": " "},
        {"raw_sha256": "invalid"},
        {"retrieved_at": datetime(2026, 11, 11)},
        {"page_count": 0},
        {"page_count": None},
        {"source_url": "not-a-url"},
        {"content_role": "specialized_medical"},
        {"target_audience": "international"},
        {"jurisdiction": "UK"},
        {"dates": {"publication_year": "2026"}},
    ],
)
def test_document_rejects_invalid_provenance_or_default_scope(change):
    with pytest.raises(ValidationError):
        DocumentVersion.model_validate(document_payload() | change)


@pytest.mark.parametrize("decision", ["blocked_usage", "pending_access", "reference_only"])
def test_disabled_selection_cannot_be_default(decision):
    with pytest.raises(ValidationError):
        SourceEligibility(decision=decision, default_index=True, eligible_sections=["Guidance"])


def test_http_200_html_shell_is_failed_pdf_acquisition():
    record = AcquisitionRecord(
        document_id="manual",
        requested_url="https://example.gov/manual.pdf",
        expected_format="pdf",
        detected_format="html",
        outcome="unexpected_format",
        checked_at=NOW,
        status_code=200,
        error="HTML application shell, not a PDF",
    )
    assert record.outcome == "unexpected_format"
    with pytest.raises(ValidationError):
        AcquisitionRecord.model_validate(
            record.model_dump() | {"outcome": "success", "error": None}
        )


def test_valid_success_requires_content_identity_and_acquisition_time():
    values = {
        "document_id": "manual",
        "requested_url": "https://example.gov/manual.pdf",
        "resolved_url": "https://example.gov/manual.pdf",
        "expected_format": "pdf",
        "detected_format": "pdf",
        "outcome": "success",
        "checked_at": NOW,
        "retrieved_at": NOW,
        "status_code": 200,
        "byte_count": 100,
        "raw_sha256": "b" * 64,
        "raw_path": "data/manual.pdf",
    }
    assert AcquisitionRecord.model_validate(values).byte_count == 100
    with pytest.raises(ValidationError):
        AcquisitionRecord.model_validate(values | {"raw_sha256": None})


@pytest.mark.parametrize(
    "location",
    [
        {"format": "pdf", "extraction_method": "native_pdf"},
        {"format": "pdf", "extraction_method": "native_pdf", "physical_page": 0},
        {"format": "html", "extraction_method": "html", "physical_page": 1},
        {"format": "pdf", "extraction_method": "ocr", "physical_page": 1, "ocr_confidence": 101},
        {
            "format": "pdf",
            "extraction_method": "native_pdf",
            "physical_page": 1,
            "bbox": [5, 0, 1, 9],
        },
    ],
)
def test_invalid_original_locations_fail(location):
    with pytest.raises(ValidationError):
        SourceLocation.model_validate(location)


def test_pdf_table_keeps_physical_printed_pages_units_and_notes():
    block = ContentBlock(
        block_id="block_example",
        document_version_id="docv_example",
        block_type="table_row",
        heading_path=["Storage"],
        sequence_index=0,
        text="A reviewed labeled source row",
        source_locations=[
            {
                "format": "pdf",
                "extraction_method": "native_pdf",
                "physical_page": 3,
                "printed_page_label": "2",
                "bbox": [0, 10, 50, 80],
            }
        ],
        table_context={
            "title": "Storage chart",
            "column_headers": ["Refrigerator", "Freezer"],
            "units": ["days", "months"],
            "footnotes": ["Preserve source conditions"],
        },
    )
    assert block.source_locations[0].physical_page != int(
        block.source_locations[0].printed_page_label
    )
    assert block.table_context.units == ("days", "months")
    with pytest.raises(ValidationError):
        ContentBlock.model_validate(block.model_dump() | {"table_context": None})
