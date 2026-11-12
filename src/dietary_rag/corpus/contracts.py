"""Format-neutral acquisition and evidence provenance contracts."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    AnyHttpUrl,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StrictBool,
    StringConstraints,
    model_validator,
)

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
NonnegativeInt = Annotated[int, Field(strict=True, ge=0)]
SourceFormat = Literal["html", "pdf"]
SourceTier = Literal[
    "official_guidance", "institutional_reference", "editorial_reference", "international_reference"
]
ContentRole = Literal[
    "consumer_guidance",
    "specialized_medical",
    "scientific_background",
    "historical_medical_reference",
    "food_service_regulatory",
    "international_supplement",
    "discovery",
]
SelectionDecision = Literal[
    "approved_with_exclusions",
    "pending_access",
    "pending_rights_review",
    "blocked_usage",
    "reference_only",
    "discovery_only",
    "rejected_format",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class SourceDates(Contract):
    publication_year: Annotated[int, Field(strict=True, ge=1900, le=2100)] | None = None
    edition: NonEmpty | None = None
    source_stated_dates: tuple[NonEmpty, ...] = ()
    structured_metadata_observed: tuple[NonEmpty, ...] = ()
    notes: NonEmpty | None = None


class SourceEligibility(Contract):
    decision: SelectionDecision
    default_index: StrictBool = False
    eligible_sections: tuple[NonEmpty, ...] = ()
    excluded_sections: tuple[NonEmpty, ...] = ()
    notes: tuple[NonEmpty, ...] = ()

    @model_validator(mode="after")
    def validate_default(self):
        if self.default_index and (
            self.decision != "approved_with_exclusions" or not self.eligible_sections
        ):
            raise ValueError("default selection requires approval and eligible sections")
        return self


class RightsReview(Contract):
    status: NonEmpty
    scope: NonEmpty
    policy_url: AnyHttpUrl | None = None
    observations: tuple[NonEmpty, ...] = ()


class AcquisitionRecord(Contract):
    document_id: Identifier
    requested_url: AnyHttpUrl
    resolved_url: AnyHttpUrl | None = None
    expected_format: SourceFormat
    detected_format: SourceFormat | None = None
    outcome: Literal[
        "success", "http_error", "network_error", "unexpected_format", "invalid_document"
    ]
    checked_at: AwareDatetime
    retrieved_at: AwareDatetime | None = None
    status_code: Annotated[int, Field(strict=True, ge=100, le=599)] | None = None
    content_type: NonEmpty | None = None
    byte_count: NonnegativeInt | None = None
    raw_sha256: Sha256 | None = None
    raw_path: Path | None = None
    error: NonEmpty | None = None

    @model_validator(mode="after")
    def validate_outcome(self):
        if self.outcome == "success":
            if (
                self.status_code != 200
                or self.detected_format != self.expected_format
                or self.resolved_url is None
                or self.retrieved_at is None
                or self.raw_sha256 is None
                or self.raw_path is None
                or not self.byte_count
                or self.error is not None
            ):
                raise ValueError(
                    "success requires correct-format HTTP 200 content and full provenance"
                )
        elif self.error is None:
            raise ValueError("failed acquisition requires an explicit error")
        if self.retrieved_at is not None and self.retrieved_at > self.checked_at:
            raise ValueError("retrieved_at cannot be later than checked_at")
        return self


class DocumentVersion(Contract):
    document_id: Identifier
    document_version_id: Identifier
    family_id: Identifier
    document_name: NonEmpty
    publisher: NonEmpty
    source_url: AnyHttpUrl
    resolved_url: AnyHttpUrl | None = None
    format: SourceFormat
    dates: SourceDates = Field(default_factory=SourceDates)
    retrieved_at: AwareDatetime
    source_tier: SourceTier
    jurisdiction: NonEmpty
    target_audience: NonEmpty
    intended_audience: NonEmpty
    content_role: ContentRole
    eligibility: SourceEligibility
    rights_review: RightsReview
    raw_sha256: Sha256
    content_sha256: Sha256 | None = None
    parser_version: NonEmpty | None = None
    page_count: PositiveInt | None = None
    text_classification: (
        Literal[
            "selectable_text", "mostly_selectable_with_sparse_pages", "scanned", "mixed", "unknown"
        ]
        | None
    ) = None
    author: NonEmpty | None = None
    medical_reviewer: NonEmpty | None = None

    @model_validator(mode="after")
    def validate_applicability(self):
        if self.eligibility.default_index and (
            self.content_role != "consumer_guidance"
            or self.target_audience != "US"
            or not (self.jurisdiction == "US" or self.jurisdiction.startswith("US-"))
        ):
            raise ValueError("default consumer selection requires applicable US consumer guidance")
        if self.format == "pdf" and self.page_count is None:
            raise ValueError("an acquired PDF version requires a physical page count")
        if self.format == "html" and (
            self.page_count is not None or self.text_classification is not None
        ):
            raise ValueError("HTML versions cannot carry PDF inspection fields")
        return self


class SourceLocation(Contract):
    format: SourceFormat
    source_anchor: NonEmpty | None = None
    dom_path: NonEmpty | None = None
    physical_page: PositiveInt | None = None  # One-based physical PDF page index.
    printed_page_label: NonEmpty | None = None
    bbox: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat] | None = None
    extraction_method: Literal["html", "native_pdf", "ocr"]
    ocr_confidence: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None = None
    review_state: Literal["pending", "reviewed", "quarantined"] = "pending"

    @model_validator(mode="after")
    def validate_location(self):
        if self.format == "html":
            if self.extraction_method != "html" or any(
                value is not None
                for value in (self.physical_page, self.printed_page_label, self.bbox)
            ):
                raise ValueError("HTML locations cannot carry PDF page/geometry fields")
        elif self.dom_path is not None:
            raise ValueError("DOM paths belong only to HTML locations")
        elif self.physical_page is None or self.extraction_method == "html":
            raise ValueError("PDF locations require a physical page and PDF/OCR extraction method")
        if self.ocr_confidence is not None and self.extraction_method != "ocr":
            raise ValueError("OCR confidence belongs only to OCR locations")
        if self.bbox is not None:
            x0, y0, x1, y1 = self.bbox
            if min(x0, y0) < 0 or x1 < x0 or y1 < y0:
                raise ValueError("bbox must be nonnegative ordered x0,y0,x1,y1 coordinates")
        return self


class TableContext(Contract):
    title: NonEmpty
    column_headers: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    row_headers: tuple[NonEmpty, ...] = ()
    units: tuple[NonEmpty, ...] = ()
    footnotes: tuple[NonEmpty, ...] = ()


class ContentBlock(Contract):
    block_id: Identifier
    document_version_id: Identifier
    block_type: Literal["heading", "paragraph", "recommendation_list", "table", "table_row", "note"]
    heading_path: tuple[NonEmpty, ...] = ()
    sequence_index: NonnegativeInt
    text: NonEmpty
    source_locations: Annotated[tuple[SourceLocation, ...], Field(min_length=1)]
    table_context: TableContext | None = None

    @model_validator(mode="after")
    def validate_table(self):
        if self.block_type in ("table", "table_row") and self.table_context is None:
            raise ValueError("table evidence requires its title and column context")
        formats = {location.format for location in self.source_locations}
        if len(formats) != 1:
            raise ValueError("a block must come from one original format")
        return self
