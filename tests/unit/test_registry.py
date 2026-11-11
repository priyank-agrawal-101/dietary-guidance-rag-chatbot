from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from dietary_rag.corpus.registry import SourceRegistry, load_registry


def test_existing_registry_keeps_sources_dates_decisions_and_one_default_per_family(
    reviewed_registry,
):
    registry = reviewed_registry
    assert len(registry.sources) == 68
    assert set(registry.policy.existing_document_ids) <= {s.document_id for s in registry.sources}
    candidates = registry.consumer_sources()
    assert len(candidates) == len({s.family_id for s in candidates}) == 16
    assert len([s for s in candidates if s.format == "pdf"]) == 6
    assert registry.get("niddk_ckd_nutrition").dates.structured_metadata_observed
    assert registry.get("dga_2025_2030_pdf").dates.publication_year is None
    assert registry.get("mn_cross_contamination").selection.default_index is False
    assert registry.get("mn_cross_contamination_pdf").selection.default_index is True


@pytest.mark.parametrize(
    "document_id, reason",
    [
        ("cleveland_balanced_diet", "rights_not_cleared_for_local_text"),
        ("healthline_food_labels", "selection_not_approved"),
        ("foodsafety_cold_storage", "access_not_verified"),
        ("who_five_keys_english_pdf", "format_not_verified"),
        ("dgac_2025_scientific_pdf", "not_consumer_guidance"),
        ("fda_food_code_2026", "not_consumer_guidance"),
        ("niddk_ckd_nutrition", "not_consumer_guidance"),
        ("who_healthy_diet", "not_us_applicable"),
        ("mn_storage_chart_pdf", "not_proposed_default"),
    ],
)
def test_nonconsumer_and_disabled_sources_are_retained_but_not_selected(
    reviewed_registry, document_id, reason
):
    result = reviewed_registry.consumer_eligibility(document_id)
    assert not result.eligible_for_consumer_ingestion
    assert reason in result.reasons
    assert reviewed_registry.get(document_id).document_id == document_id


def test_unknown_source_does_not_fall_back_to_consumer_corpus(reviewed_registry):
    with pytest.raises(KeyError):
        reviewed_registry.consumer_eligibility("unknown")


@pytest.mark.parametrize(
    "change",
    [
        {"rights_review": {"status": "unknown_new_permission_status", "scope": "Unresolved"}},
        {"content_role": "specialized_medical"},
        {"target_audience": "international"},
        {"detected_format": None},
    ],
)
def test_proposed_defaults_cannot_bypass_mandatory_filters(reviewed_registry, change):
    values = reviewed_registry.model_dump()
    for source in values["sources"]:
        if source["document_id"] == "mn_cold_storage":
            source.update(change)
    registry = SourceRegistry.model_validate(values)
    assert not registry.consumer_eligibility("mn_cold_storage").eligible_for_consumer_ingestion


def test_conflicting_default_assets_fail_instead_of_choosing_one(reviewed_registry):
    values = reviewed_registry.model_dump()
    for source in values["sources"]:
        if source["document_id"] == "mn_cross_contamination":
            source["selection"]["default_index"] = True
    with pytest.raises(ValidationError, match="one guidance family"):
        SourceRegistry.model_validate(values)


@pytest.mark.parametrize(
    "mutation", ["duplicate_id", "missing_existing", "wrong_family", "stale_summary"]
)
def test_invalid_identity_relationships_or_counts_fail(reviewed_registry, mutation):
    values = deepcopy(reviewed_registry.model_dump())
    if mutation == "duplicate_id":
        values["sources"] += (values["sources"][0],)
    elif mutation == "missing_existing":
        values["sources"] = values["sources"][1:]
    elif mutation == "wrong_family":
        for source in values["sources"]:
            if source["document_id"] == "mn_cross_contamination":
                source["family_id"] = "wrong_family"
    else:
        values["review_summary"]["candidate_url_count"] = 100
    with pytest.raises(ValidationError):
        SourceRegistry.model_validate(values)


def test_duplicate_yaml_keys_are_not_silently_overwritten(tmp_path):
    registry_path = tmp_path / "sources.yaml"
    registry_path.write_text("schema_version: 1\nschema_version: 1\n")
    with pytest.raises(ValueError, match="duplicate YAML key"):
        load_registry(registry_path)


def test_loading_registry_does_not_create_a_corpus(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(tmp_path)
    assert len(load_registry(root / "config/sources.yaml").sources) == 68
    assert list(tmp_path.iterdir()) == []
