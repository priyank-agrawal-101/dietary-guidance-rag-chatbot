from dietary_rag.corpus.coverage import audit_coverage
from dietary_rag.corpus.registry import SourceRegistry


def test_audit_counts_families_not_pdf_pages_or_alternate_urls(reviewed_registry):
    report = audit_coverage(reviewed_registry)
    assert report["candidate_url_count"] == 68
    assert report["eligible_consumer_family_count"] == 16
    assert report["verified_official_pdf_count"] == 12
    assert report["approved_official_pdf_count"] == 9
    assert report["eligible_format_counts"] == {"html": 10, "pdf": 6}
    assert report["family_target_met"]
    assert report["consumer_acquisitions"] is None
    assert not report["published_corpus_ready"]
    produce = next(
        item for item in report["alternate_families"] if item["family_id"] == "fda_produce"
    )
    assert produce["document_ids"] == ["fda_produce", "fda_produce_pdf"]
    assert produce["selected_document_ids"] == ["fda_produce"]


def test_audit_uses_passing_eligibility_not_summary_or_default_flag(reviewed_registry):
    values = reviewed_registry.model_dump()
    for source in values["sources"]:
        if source["document_id"] in ("fda_produce", "fda_seafood"):
            source["rights_review"] = {"status": "unresolved", "scope": "Not cleared"}
    registry = SourceRegistry.model_validate(values)
    assert registry.review_summary["default_selected_unique_family_count"] == 16
    report = audit_coverage(registry)
    assert report["eligible_consumer_family_count"] == 14
    assert report["family_shortfall"] == 1
    assert not report["family_target_met"]


def test_reference_conflicts_and_medical_material_are_never_counted(reviewed_registry):
    report = audit_coverage(reviewed_registry)
    selected = {item["selected_document_id"] for item in report["families"]}
    assert not selected & {
        "fda_storing_food",
        "wa_power_outages_current",
        "fda_pregnancy_food_safety",
        "nhlbi_dash_full_pdf",
        "dgac_2025_scientific_pdf",
    }
    assert {
        "cdc_emergency_food_safety",
        "cdc_pregnancy_food_safety",
        "fda_nutrition_label_current",
    } <= selected
