"""Explicit source-registry loading and fail-closed consumer acquisition selection."""

from collections import Counter
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import AnyHttpUrl, AwareDatetime, Field, StrictBool, model_validator

from dietary_rag.corpus.contracts import (
    ContentRole,
    Contract,
    Identifier,
    NonEmpty,
    NonnegativeInt,
    PositiveInt,
    RightsReview,
    Sha256,
    SourceDates,
    SourceEligibility,
    SourceFormat,
    SourceTier,
)

# These recorded statuses permit attributed local educational text review, not blanket reuse.
LOCAL_TEXT_RIGHTS = frozenset(
    {
        "permitted_government_text_with_exclusions",
        "explicit_document_reuse_notice",
        "text_reviewed_no_explicit_reuse_barrier_observed",
    }
)


class RegistrySelection(SourceEligibility):
    approved_pdf_for_milestone: StrictBool = False


class SourceAccess(Contract):
    checked_at: AwareDatetime
    status_code: Annotated[int, Field(strict=True, ge=100, le=599)] | None = None
    publicly_accessible: StrictBool
    error: NonEmpty | None = None
    content_type: NonEmpty | None = None
    bytes: NonnegativeInt | None = None
    sha256: Sha256 | None = None
    evidence_record: Path


class PDFInspection(Contract):
    page_count: PositiveInt
    text_classification: NonEmpty
    pages_with_extractable_text: NonnegativeInt
    pages_with_little_text: tuple[PositiveInt, ...]
    ocr_required_for_main_text: StrictBool
    complex_layout: StrictBool
    layout_notes: NonEmpty
    table_pages_in_sample: tuple[PositiveInt, ...]
    sampled_pages: tuple[PositiveInt, ...]
    inspection_scope: NonEmpty
    inspection_record: Path

    @model_validator(mode="after")
    def validate_pages(self):
        if self.pages_with_extractable_text > self.page_count or any(
            page > self.page_count
            for page in (
                *self.pages_with_little_text,
                *self.table_pages_in_sample,
                *self.sampled_pages,
            )
        ):
            raise ValueError("PDF inspection pages exceed physical page count")
        return self


class RegistrySource(Contract):
    document_id: Identifier
    document_name: NonEmpty
    publisher: NonEmpty
    source_url: AnyHttpUrl
    resolved_url: AnyHttpUrl | None = None
    source_tier: SourceTier
    jurisdiction: NonEmpty
    target_audience: NonEmpty
    intended_audience: NonEmpty
    content_role: ContentRole
    coverage: NonEmpty
    format: SourceFormat
    detected_format: SourceFormat | None = None
    parser_profile: NonEmpty
    origin: NonEmpty
    family_id: Identifier
    dates: SourceDates
    author: NonEmpty | None = None
    medical_reviewer: NonEmpty | None = None
    access: SourceAccess
    selection: RegistrySelection
    rights_review: RightsReview
    direct_pdf_url: AnyHttpUrl | None = None
    source_page_url: AnyHttpUrl | None = None
    pdf: PDFInspection | None = None
    alternate_document_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_pdf(self):
        if self.pdf is not None and (
            self.format != "pdf"
            or self.detected_format != "pdf"
            or self.direct_pdf_url is None
            or self.direct_pdf_url != self.resolved_url
            or not self.access.publicly_accessible
            or self.access.status_code != 200
            or self.access.error is not None
            or not self.access.bytes
            or self.access.sha256 is None
        ):
            raise ValueError(
                "verified PDF inspection requires matching format and direct/resolved URL"
            )
        if self.selection.approved_pdf_for_milestone and (
            self.pdf is None
            or self.selection.decision != "approved_with_exclusions"
            or self.source_tier != "official_guidance"
            or not (self.jurisdiction == "US" or self.jurisdiction.startswith("US-"))
        ):
            raise ValueError("milestone PDF approval requires verified approved official US PDF")
        return self


class RegistryPolicy(Contract):
    allowed_formats: Annotated[tuple[SourceFormat, ...], Field(min_length=1)]
    audience: Literal["US"]
    initial_target_unique_consumer_families: NonEmpty
    minimum_verified_official_us_pdfs: Annotated[int, Field(strict=True, ge=5)]
    existing_document_ids: tuple[Identifier, ...]
    default_content_roles: tuple[ContentRole, ...]
    blocked_decisions: tuple[NonEmpty, ...]
    approval_meaning: NonEmpty
    activation_gates: tuple[NonEmpty, ...]
    international_policy: NonEmpty
    pdf_detection: NonEmpty
    date_policy: NonEmpty


class ConsumerEligibility(Contract):
    document_id: Identifier
    eligible_for_consumer_ingestion: StrictBool
    reasons: tuple[str, ...]
    remaining_activation_gates: tuple[str, ...]


class SourceRegistry(Contract):
    schema_version: Literal[1]
    review_date: date
    status: NonEmpty
    policy: RegistryPolicy
    review_summary: dict[str, Any]
    sources: tuple[RegistrySource, ...]

    @model_validator(mode="after")
    def validate_integrity(self):
        by_id = {source.document_id: source for source in self.sources}
        if len(by_id) != len(self.sources):
            raise ValueError("duplicate registry document IDs")
        if not set(self.policy.existing_document_ids) <= by_id.keys():
            raise ValueError("existing source IDs are missing")
        families = [source.family_id for source in self.sources if source.selection.default_index]
        if len(families) != len(set(families)):
            raise ValueError("multiple proposed default assets in one guidance family")
        for source in self.sources:
            for alternate_id in source.alternate_document_ids:
                alternate = by_id.get(alternate_id)
                if alternate is None or alternate_id == source.document_id:
                    raise ValueError("unknown or self-referencing alternate source")
                if alternate.family_id != source.family_id:
                    raise ValueError("alternate sources must belong to the same guidance family")
                if source.document_id not in alternate.alternate_document_ids:
                    raise ValueError("alternate source relationships must be reciprocal")
        approved_pdfs = sum(source.selection.approved_pdf_for_milestone for source in self.sources)
        if approved_pdfs < self.policy.minimum_verified_official_us_pdfs:
            raise ValueError("fewer verified approved official PDFs than the registry minimum")
        expected_counts = {
            "candidate_url_count": len(self.sources),
            "preserved_existing_source_count": len(self.policy.existing_document_ids),
            "approved_official_us_pdf_count": approved_pdfs,
            "verified_official_us_pdf_count": sum(
                source.pdf is not None
                and source.source_tier == "official_guidance"
                and (source.jurisdiction == "US" or source.jurisdiction.startswith("US-"))
                for source in self.sources
            ),
            "default_selected_asset_count": len(families),
            "default_selected_unique_family_count": len(set(families)),
            "decision_counts": dict(Counter(source.selection.decision for source in self.sources)),
        }
        if any(self.review_summary.get(key) != value for key, value in expected_counts.items()):
            raise ValueError("registry review summary is stale")
        return self

    def get(self, document_id: str) -> RegistrySource:
        for source in self.sources:
            if source.document_id == document_id:
                return source
        raise KeyError(document_id)

    def consumer_eligibility(self, document_id: str) -> ConsumerEligibility:
        source = self.get(document_id)
        reasons = []
        if source.selection.decision != "approved_with_exclusions":
            reasons.append("selection_not_approved")
        if source.selection.decision in self.policy.blocked_decisions:
            reasons.append("selection_disabled_by_policy")
        if not source.selection.default_index:
            reasons.append("not_proposed_default")
        if (
            source.content_role != "consumer_guidance"
            or source.content_role not in self.policy.default_content_roles
        ):
            reasons.append("not_consumer_guidance")
        if (
            source.target_audience != "US"
            or source.source_tier == "international_reference"
            or not (source.jurisdiction == "US" or source.jurisdiction.startswith("US-"))
        ):
            reasons.append("not_us_applicable")
        if source.rights_review.status not in LOCAL_TEXT_RIGHTS:
            reasons.append("rights_not_cleared_for_local_text")
        if (
            not source.access.publicly_accessible
            or source.access.status_code != 200
            or source.access.error
        ):
            reasons.append("access_not_verified")
        if (
            source.detected_format != source.format
            or source.format not in self.policy.allowed_formats
        ):
            reasons.append("format_not_verified")
        if not source.access.bytes or not source.access.sha256 or not source.resolved_url:
            reasons.append("acquisition_provenance_incomplete")
        if source.format == "pdf" and source.pdf is None:
            reasons.append("pdf_not_verified")
        if not source.selection.eligible_sections:
            reasons.append("no_eligible_sections")
        return ConsumerEligibility(
            document_id=document_id,
            eligible_for_consumer_ingestion=not reasons,
            reasons=tuple(reasons),
            remaining_activation_gates=self.policy.activation_gates,
        )

    def consumer_sources(self) -> tuple[RegistrySource, ...]:
        """Reviewed acquisition candidates, not active indexed documents."""
        return tuple(
            source
            for source in self.sources
            if self.consumer_eligibility(source.document_id).eligible_for_consumer_ingestion
        )


class _UniqueKeyLoader(yaml.SafeLoader):
    """Do not silently accept overwritten YAML IDs or policy keys."""


def _unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ValueError("source registry mapping keys must be strings")
        if key in mapping:
            raise ValueError("source registry contains a duplicate YAML key")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def load_registry(path: Path) -> SourceRegistry:
    """Read/validate explicitly; no fetches, cache reads, registry edits or activation."""
    try:
        values = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise ValueError("source registry contains invalid YAML") from exc
    return SourceRegistry.model_validate(values)
