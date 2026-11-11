"""Read-only coverage accounting; source selection is distinct from a published index."""

from collections import Counter
from typing import Any

from dietary_rag.corpus.artifacts import ArtifactIntegrityError
from dietary_rag.corpus.identity import stable_id
from dietary_rag.corpus.registry import SourceRegistry
from dietary_rag.corpus.repository import CorpusRepository


def audit_coverage(
    registry: SourceRegistry,
    *,
    repository: CorpusRepository | None = None,
    minimum_consumer_families: int = 15,
) -> dict[str, Any]:
    """Compute from passing eligibility, not URL/page counts or historical acquisitions.

    Optional acquisition verification checks current-registry document versions and
    actual artifact bytes. The caller opens a read-only repository; this function
    never downloads, creates stores, changes approvals or publishes a corpus.
    """
    if minimum_consumer_families < 1:
        raise ValueError("minimum consumer family count must be positive")
    selected = registry.consumer_sources()
    families = {source.family_id for source in selected}
    verified_pdfs = [
        source
        for source in registry.sources
        if source.pdf is not None
        and source.source_tier == "official_guidance"
        and (source.jurisdiction == "US" or source.jurisdiction.startswith("US-"))
    ]
    approved_pdfs = [
        source for source in verified_pdfs if source.selection.approved_pdf_for_milestone
    ]
    family_members: dict[str, list[str]] = {}
    for source in registry.sources:
        family_members.setdefault(source.family_id, []).append(source.document_id)
    current_snapshot = stable_id("registry", {"registry": registry})
    acquisitions = None
    if repository is not None:
        matched = []
        missing = []
        for source in selected:
            versions = repository.document_versions(
                source.document_id, registry_snapshot_id=current_snapshot
            )
            valid = []
            errors = []
            for version in versions:
                if version.raw_sha256 != source.access.sha256:
                    errors.append("reviewed_hash_mismatch")
                    continue
                try:
                    artifact = repository.artifacts.describe(version.raw_sha256)
                except (OSError, ArtifactIntegrityError) as exc:
                    errors.append(f"artifact_unavailable_or_corrupt: {type(exc).__name__}")
                    continue
                if artifact.byte_count != source.access.bytes:
                    errors.append("reviewed_size_mismatch")
                    continue
                valid.append(version)
            if valid:
                latest = max(
                    valid, key=lambda version: (version.retrieved_at, version.document_version_id)
                )
                matched.append(
                    {
                        "document_id": source.document_id,
                        "family_id": source.family_id,
                        "format": source.format,
                        "document_version_id": latest.document_version_id,
                        "raw_sha256": latest.raw_sha256,
                        "retrieved_at": latest.retrieved_at.isoformat(),
                    }
                )
            else:
                missing.append(
                    {
                        "document_id": source.document_id,
                        "reasons": sorted(set(errors)) or ["no_current_registry_document_version"],
                    }
                )
        acquired_pdf_ids = sorted(
            item["document_id"]
            for item in matched
            if item["format"] == "pdf"
            and registry.get(item["document_id"]).selection.approved_pdf_for_milestone
        )
        acquisitions = {
            "verification_scope": "Current registry snapshot and exact stored artifact bytes; not extracted/indexed evidence",
            "matched_consumer_document_count": len(matched),
            "matched_consumer_family_count": len({item["family_id"] for item in matched}),
            "matched_approved_official_pdf_count": len(acquired_pdf_ids),
            "matched_approved_official_pdf_ids": acquired_pdf_ids,
            "minimum_pdf_acquisitions_met": len(acquired_pdf_ids)
            >= registry.policy.minimum_verified_official_us_pdfs,
            "missing_or_invalid": missing,
            "documents": matched,
        }
    return {
        "status": "source_acquisition_coverage_audit",
        "review_date": registry.review_date.isoformat(),
        "registry_snapshot_id": current_snapshot,
        "candidate_url_count": len(registry.sources),
        "preserved_existing_source_count": len(registry.policy.existing_document_ids),
        "eligible_consumer_asset_count": len(selected),
        "eligible_consumer_family_count": len(families),
        "minimum_consumer_families": minimum_consumer_families,
        "family_shortfall": max(0, minimum_consumer_families - len(families)),
        "family_target_met": len(families) >= minimum_consumer_families,
        "eligible_format_counts": dict(Counter(source.format for source in selected)),
        "verified_official_pdf_count": len(verified_pdfs),
        "approved_official_pdf_count": len(approved_pdfs),
        "minimum_verified_official_pdfs": registry.policy.minimum_verified_official_us_pdfs,
        "approved_pdf_registry_target_met": len(approved_pdfs)
        >= registry.policy.minimum_verified_official_us_pdfs,
        "families": [
            {
                "family_id": source.family_id,
                "selected_document_id": source.document_id,
                "format": source.format,
                "publisher": source.publisher,
                "coverage": source.coverage,
                "eligible_sections": source.selection.eligible_sections,
                "excluded_sections": source.selection.excluded_sections,
            }
            for source in selected
        ],
        "alternate_families": [
            {
                "family_id": family,
                "document_ids": members,
                "selected_document_ids": [
                    source.document_id for source in selected if source.family_id == family
                ],
            }
            for family, members in family_members.items()
            if len(members) > 1
        ],
        "recorded_coverage_gaps": registry.review_summary.get("coverage_gaps", []),
        "consumer_acquisitions": acquisitions,
        "published_corpus_ready": False,
        "remaining_activation_gates": registry.policy.activation_gates,
    }
