"""Reviewed OCR routing, image provenance and quarantined candidate evidence."""

from typing import Annotated, Literal

from pydantic import Field, FiniteFloat, model_validator

from dietary_rag.corpus.contracts import (
    ContentBlock,
    Contract,
    DocumentVersion,
    Identifier,
    NonEmpty,
    PositiveInt,
    Sha256,
    SourceLocation,
)

Box = tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
Confidence = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


class OCRRegion(Contract):
    key: Identifier
    bbox: Box
    section: NonEmpty
    kind: Literal["prose", "table"] = "prose"
    rotation: Literal[0, 90, 180, 270] = 0  # Counterclockwise correction, not source rotation.
    skew_degrees: Annotated[float, Field(ge=-5, le=5, allow_inf_nan=False)] = 0

    @model_validator(mode="after")
    def valid_box(self):
        x0, y0, x1, y1 = self.bbox
        if min(x0, y0) < 0 or x1 <= x0 or y1 <= y0:
            raise ValueError("OCR region requires a positive original-page rectangle")
        return self


class OCRPagePlan(Contract):
    physical_page: PositiveInt
    intentional_blank: bool = False
    printed_page_label: NonEmpty | None = None  # Explicit reviewed label; never inferred from OCR.
    regions: tuple[OCRRegion, ...] = ()


class OCRPlan(Contract):
    raw_sha256: Sha256
    document_version_id: Identifier
    fixture_kind: Literal["project_created_scan", "source_derived_synthetic_scan", "original_pdf"]
    review_note: NonEmpty
    pages: tuple[OCRPagePlan, ...]
    dpi: Annotated[int, Field(strict=True, ge=150, le=400)] = 300
    language: Annotated[str, Field(pattern=r"^[a-z]{3}(\+[a-z]{3})*$")] = "eng"
    page_segmentation_mode: Literal[3, 4, 6] = 6
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=30)] = 15
    minimum_confidence: Confidence = 85
    critical_confidence: Confidence = 95

    @model_validator(mode="after")
    def routing(self):
        if len(self.pages) > 100 or len({p.physical_page for p in self.pages}) != len(self.pages):
            raise ValueError("OCR page plans must be unique and bounded at 100 pages")
        if sum(len(p.regions) for p in self.pages) > 16:
            raise ValueError("OCR is bounded at 16 reviewed regions per invocation")
        if self.minimum_confidence < 85 or self.critical_confidence < 95:
            raise ValueError("OCR review thresholds cannot weaken the quality gate")
        for page in self.pages:
            if page.intentional_blank and page.regions:
                raise ValueError("intentional blank pages cannot request OCR")
            if len({r.key for r in page.regions}) != len(page.regions):
                raise ValueError("OCR region keys must be unique on their page")
        return self


class OCRPageClassification(Contract):
    physical_page: PositiveInt
    width: FiniteFloat
    height: FiniteFloat
    rotation: int
    native_word_count: int
    native_quality: Annotated[float, Field(ge=0, le=1)]
    image_area_fraction: Annotated[float, Field(ge=0, le=1)]
    classification: Literal["native", "sparse_native", "blank", "scanned", "mixed", "visual_only"]
    route: Literal["native_only", "skip_blank", "ocr_regions", "needs_review"]
    reason: NonEmpty


class OCRImage(Contract):
    image_id: Identifier
    physical_page: PositiveInt
    region_key: Identifier
    original_page_png_sha256: Sha256
    processed_png_sha256: Sha256
    original_pixel_size: tuple[PositiveInt, PositiveInt]
    original_page_size: tuple[FiniteFloat, FiniteFloat]
    processed_pixel_size: tuple[PositiveInt, PositiveInt]
    crop_pixel_box: tuple[int, int, int, int]
    region_bbox: Box
    rotation: int
    skew_degrees: FiniteFloat
    dpi: PositiveInt


class OCRWord(Contract):
    word_id: Identifier
    image_id: Identifier
    text: NonEmpty
    confidence: Confidence
    location: SourceLocation
    processed_bbox: Box
    line_key: tuple[int, int, int]
    critical: bool


class OCRCandidate(Contract):
    candidate_id: Identifier
    image_id: Identifier
    text: NonEmpty
    word_ids: tuple[Identifier, ...]
    source_location: SourceLocation
    state: Literal["pending", "quarantined"]
    reasons: tuple[NonEmpty, ...]
    block_id: Identifier | None = None


class OCRExtraction(Contract):
    extraction_id: Identifier
    configuration_id: Identifier
    adapter_version: NonEmpty
    document: DocumentVersion
    fixture_kind: NonEmpty
    engine: dict[str, str]
    pages: tuple[OCRPageClassification, ...]
    images: tuple[OCRImage, ...]
    words: tuple[OCRWord, ...]  # Administrative candidates, including quarantined text.
    candidates: tuple[OCRCandidate, ...]
    blocks: tuple[ContentBlock, ...]  # Only candidates that passed automatic quality checks.
    diagnostics: tuple[NonEmpty, ...]
    review_state: Literal["pending", "quarantined"]

    @model_validator(mode="after")
    def relationships(self):
        if [p.physical_page for p in self.pages] != list(range(1, self.document.page_count + 1)):
            raise ValueError("OCR classifications must cover every original physical page")
        images = {i.image_id: i for i in self.images}
        words = {w.word_id: w for w in self.words}
        blocks = {b.block_id: b for b in self.blocks}
        if (
            len(images) != len(self.images)
            or len(words) != len(self.words)
            or len(blocks) != len(self.blocks)
        ):
            raise ValueError("OCR evidence identities must be unique")
        for word in self.words:
            image = images.get(word.image_id)
            loc = word.location
            if (
                image is None
                or loc.physical_page != image.physical_page
                or loc.extraction_method != "ocr"
                or loc.bbox is None
                or loc.ocr_confidence != word.confidence
            ):
                raise ValueError("OCR words require original image/page/confidence provenance")
            page = self.pages[loc.physical_page - 1]
            if loc.bbox[2] > page.width or loc.bbox[3] > page.height:
                raise ValueError("OCR word exceeds original page")
        retained = set()
        for candidate in self.candidates:
            if (
                candidate.image_id not in images
                or not candidate.word_ids
                or not set(candidate.word_ids) <= words.keys()
            ):
                raise ValueError("OCR candidates must resolve to original image and words")
            if any(words[w].image_id != candidate.image_id for w in candidate.word_ids):
                raise ValueError("OCR candidates cannot mix source images")
            if candidate.source_location.review_state != candidate.state:
                raise ValueError("OCR candidate quality state differs from location")
            if candidate.state == "quarantined":
                if (
                    candidate.block_id is not None
                    or not candidate.reasons
                    or self.review_state != "quarantined"
                ):
                    raise ValueError("quarantined OCR cannot resolve to retained evidence")
            elif candidate.block_id not in blocks:
                raise ValueError("passing OCR candidate must resolve to its block")
            else:
                block = blocks[candidate.block_id]
                if (
                    candidate.reasons
                    or block.text != candidate.text
                    or block.source_locations != (candidate.source_location,)
                ):
                    raise ValueError("retained OCR block differs from its passing candidate")
                if any(
                    words[w].confidence < 85 or (words[w].critical and words[w].confidence < 95)
                    for w in candidate.word_ids
                ):
                    raise ValueError("uncertain OCR numeric/word content cannot be retained")
                retained.add(candidate.block_id)
        if retained != blocks.keys():
            raise ValueError("only passing OCR candidates may produce blocks")
        for index, block in enumerate(self.blocks):
            if (
                block.sequence_index != index
                or block.document_version_id != self.document.document_version_id
                or any(
                    loc.extraction_method != "ocr" or loc.review_state != "pending"
                    for loc in block.source_locations
                )
            ):
                raise ValueError(
                    "OCR blocks must be ordered, pending and bound to their original version"
                )
        return self
