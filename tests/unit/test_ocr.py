"""Fast routing/quality cases use deterministic TSV; real OCR lives in integration."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from dietary_rag.corpus.contracts import DocumentVersion
from dietary_rag.ingestion.ocr import OCRExtractionError, OCRExtractor, _original_box
from dietary_rag.ingestion.ocr_contracts import (
    OCRExtraction,
    OCRImage,
    OCRPagePlan,
    OCRPlan,
    OCRRegion,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ocr"


def inputs():
    return (
        FIXTURES / "project-mixed-scan.pdf",
        DocumentVersion.model_validate_json((FIXTURES / "document.json").read_text()),
        OCRPlan.model_validate_json((FIXTURES / "plan.json").read_text()),
    )


def row(text="Keep", conf=98, left=30, top=30, width=100, height=40, block=1, par=1, line=1):
    return {
        "text": text,
        "conf": str(conf),
        "left": str(left),
        "top": str(top),
        "width": str(width),
        "height": str(height),
        "block_num": str(block),
        "par_num": str(par),
        "line_num": str(line),
    }


class Engine:
    metadata = {"name": "deterministic_test_engine", "version": "fixture_v1", "language": "eng"}

    def __init__(self, rows=None, error=False):
        self.rows = rows if rows is not None else [row()]
        self.calls = []
        self.error = error

    def recognize(self, path, plan):
        self.calls.append((path, plan))
        if self.error:
            raise OCRExtractionError("deliberate timeout")
        return self.rows


def extract(tmp_path, plan=None, engine=None):
    path, document, default = inputs()
    return OCRExtractor(plan or default, engine=engine or Engine()).extract_report(
        path, document, output_dir=tmp_path
    )


def test_routing_skips_native_sparse_and_both_blank_pages(tmp_path):
    engine = Engine()
    result = extract(tmp_path, engine=engine)
    assert [(p.classification, p.route) for p in result.pages] == [
        ("native", "native_only"),
        ("blank", "skip_blank"),
        ("sparse_native", "native_only"),
        ("scanned", "ocr_regions"),
        ("mixed", "ocr_regions"),
        ("scanned", "ocr_regions"),
        ("scanned", "ocr_regions"),
        ("scanned", "ocr_regions"),
        ("blank", "skip_blank"),
    ]
    assert len(engine.calls) == 5
    assert {w.location.physical_page for w in result.words} == {4, 5, 6, 7, 8}
    assert all("Selectable native heading" not in b.text for b in result.blocks)
    assert result.words[0].location.printed_page_label == "7"
    assert OCRExtraction.model_validate_json(result.model_dump_json()) == result
    assert result == extract(tmp_path, engine=Engine())
    for image in result.images:
        assert len(image.original_page_png_sha256) == 64
        assert image.processed_pixel_size[0] > 0


@pytest.mark.parametrize(
    "word,conf,reason",
    [
        ("40", 91, "uncertain_numeric_or_unit"),
        ("F", 91, "uncertain_numeric_or_unit"),
        ("minutes", 91, "uncertain_numeric_or_unit"),
        ("clean", 80, "low_word_confidence"),
        ("4O", 99, "ambiguous_character_or_numeric_token"),
    ],
)
def test_uncertain_numbers_units_words_never_become_blocks(tmp_path, word, conf, reason):
    result = extract(tmp_path, engine=Engine([row(word, conf)]))
    assert result.review_state == "quarantined" and result.blocks == ()
    assert all(
        reason in c.reasons
        and c.block_id is None
        and c.source_location.review_state == "quarantined"
        for c in result.candidates
    )
    path, doc, plan = inputs()
    with pytest.raises(OCRExtractionError, match="quarantined"):
        OCRExtractor(plan, engine=Engine([row(word, conf)])).extract(path, doc, output_dir=tmp_path)


def test_high_confidence_numeric_is_pending_without_promoting_review(tmp_path):
    result = extract(tmp_path, engine=Engine([row("40", 99), row("F", 99, left=140)]))
    assert result.review_state == "pending"
    assert all(
        b.text == "40 F" and b.source_locations[0].review_state == "pending" for b in result.blocks
    )


def test_ocr_tables_remain_candidates_until_cell_review(tmp_path):
    _, _, plan = inputs()
    page = plan.pages[1]
    region = page.regions[0].model_copy(update={"kind": "table"})
    plan = plan.model_copy(update={"pages": (page.model_copy(update={"regions": (region,)}),)})
    result = extract(tmp_path, plan=plan)
    assert result.blocks == ()
    assert "ocr_table_requires_cell_and_header_review" in result.candidates[0].reasons


def test_unreviewed_scans_do_not_trigger_whole_page_ocr(tmp_path):
    _, _, plan = inputs()
    engine = Engine()
    result = extract(tmp_path, plan=plan.model_copy(update={"pages": ()}), engine=engine)
    assert not engine.calls and not result.images and not result.blocks
    assert result.review_state == "quarantined"
    assert result.pages[3].route == "needs_review"


@pytest.mark.parametrize(
    "regions,match",
    [
        ((OCRRegion(key="outside", bbox=(0, 0, 700, 800), section="Fixture"),), "exceeds"),
        (
            (OCRRegion(key="overlap_native", bbox=(0, 0, 600, 100), section="Fixture"),),
            "overlaps selectable",
        ),
        ((OCRRegion(key="no_image", bbox=(0, 100, 600, 200), section="Fixture"),), "no raster"),
        (
            (
                OCRRegion(key="a", bbox=(40, 233, 560, 380), section="Fixture"),
                OCRRegion(key="b", bbox=(40, 233, 560, 380), section="Fixture"),
            ),
            "regions overlap",
        ),
    ],
)
def test_region_bounds_and_no_native_duplication(tmp_path, regions, match):
    _, _, plan = inputs()
    page = OCRPagePlan(physical_page=5, regions=regions)
    with pytest.raises(OCRExtractionError, match=match):
        extract(tmp_path, plan=plan.model_copy(update={"pages": (page,)}))


def test_false_blank_override_is_rejected(tmp_path):
    _, _, plan = inputs()
    with pytest.raises(OCRExtractionError, match="override"):
        extract(
            tmp_path,
            plan=plan.model_copy(
                update={"pages": (OCRPagePlan(physical_page=4, intentional_blank=True),)}
            ),
        )


@pytest.mark.parametrize("rows", [[], [row(conf="nan")], [row(width=-1)], [row(left=50000)]])
def test_empty_or_malformed_engine_output_cannot_be_evidence(tmp_path, rows):
    if not rows:
        result = extract(tmp_path, engine=Engine(rows))
        assert result.review_state == "quarantined" and result.blocks == ()
    else:
        with pytest.raises(OCRExtractionError, match="invalid OCR"):
            extract(tmp_path, engine=Engine(rows))


def test_engine_failure_is_reported_and_quarantined(tmp_path):
    result = extract(tmp_path, engine=Engine(error=True))
    assert not result.blocks and len(result.diagnostics) == 5
    assert all("timeout" in d for d in result.diagnostics)


@pytest.mark.parametrize(
    "update",
    [
        {"dpi": 500},
        {"timeout_seconds": 31},
        {"minimum_confidence": 80},
        {"critical_confidence": 90},
        {"language": "eng;bad"},
    ],
)
def test_resource_and_quality_configuration_is_bounded(update):
    _, _, plan = inputs()
    data = plan.model_dump()
    data.update(update)
    with pytest.raises(ValidationError):
        OCRPlan.model_validate(data)


def test_provenance_and_review_contracts_reject_tampering(tmp_path):
    path, doc, plan = inputs()
    with pytest.raises(OCRExtractionError, match="provenance"):
        OCRExtractor(
            plan.model_copy(update={"raw_sha256": "0" * 64}), engine=Engine()
        ).extract_report(path, doc, output_dir=tmp_path)
    with pytest.raises(OCRExtractionError, match="approved"):
        OCRExtractor(plan, engine=Engine()).extract_report(
            path,
            doc.model_copy(
                update={"eligibility": doc.eligibility.model_copy(update={"default_index": False})}
            ),
            output_dir=tmp_path,
        )
    result = extract(tmp_path)
    data = result.model_dump(mode="json")
    data["words"][0]["confidence"] = 50
    data["words"][0]["location"]["ocr_confidence"] = 50
    with pytest.raises(ValidationError, match="uncertain"):
        OCRExtraction.model_validate(data)
    data = result.model_dump(mode="json")
    data["candidates"][0]["word_ids"] = ["missing"]
    with pytest.raises(ValidationError, match="resolve"):
        OCRExtraction.model_validate(data)


@pytest.mark.parametrize(
    "rotation,expected",
    [
        (0, (10, 20, 30, 40)),
        (90, (160, 10, 180, 30)),
        (180, (170, 60, 190, 80)),
        (270, (20, 70, 40, 90)),
    ],
)
def test_inverse_quarter_turns_preserve_original_coordinates(rotation, expected):
    image = OCRImage(
        image_id="image",
        physical_page=1,
        region_key="region",
        original_page_png_sha256="0" * 64,
        processed_png_sha256="1" * 64,
        original_pixel_size=(200, 100),
        original_page_size=(200, 100),
        processed_pixel_size=(100, 200) if rotation in (90, 270) else (200, 100),
        crop_pixel_box=(0, 0, 200, 100),
        region_bbox=(0, 0, 200, 100),
        rotation=rotation,
        skew_degrees=0,
        dpi=72,
    )
    assert _original_box((10, 20, 30, 40), image) == expected


def test_rendering_and_invocation_budgets_fail_before_ocr(tmp_path, monkeypatch):
    monkeypatch.setattr("dietary_rag.ingestion.ocr.MAX_PIXELS", 100)
    with pytest.raises(OCRExtractionError, match="pixel bound"):
        extract(tmp_path)
    monkeypatch.setattr("dietary_rag.ingestion.ocr.MAX_PIXELS", 20_000_000)
    monkeypatch.setattr("dietary_rag.ingestion.ocr.MAX_SECONDS", 0)
    with pytest.raises(OCRExtractionError, match="120-second budget"):
        extract(tmp_path)


def test_crop_offset_and_deskew_inverse_preserve_page_points():
    image = OCRImage(
        image_id="image",
        physical_page=1,
        region_key="region",
        original_page_png_sha256="0" * 64,
        processed_png_sha256="1" * 64,
        original_pixel_size=(400, 200),
        original_page_size=(200, 100),
        processed_pixel_size=(200, 100),
        crop_pixel_box=(100, 50, 300, 150),
        region_bbox=(50, 25, 150, 75),
        rotation=0,
        skew_degrees=0,
        dpi=144,
    )
    assert _original_box((10, 20, 30, 40), image) == (55, 35, 65, 45)
    corrected = image.model_copy(update={"skew_degrees": 2})
    b = _original_box((90, 40, 110, 60), corrected)
    assert (b[0] + b[2]) / 2 == pytest.approx(100)
    assert (b[1] + b[3]) / 2 == pytest.approx(50)


def test_plan_rejects_duplicate_pages_too_many_regions_and_excess_skew():
    _, _, plan = inputs()
    data = plan.model_dump(mode="json")
    data["pages"] *= 2
    with pytest.raises(ValidationError, match="unique"):
        OCRPlan.model_validate(data)
    with pytest.raises(ValidationError):
        OCRRegion(key="skew", bbox=(0, 0, 10, 10), section="Fixture", skew_degrees=6)
    data = plan.model_dump(mode="json")
    data["pages"] = [
        {
            "physical_page": 4,
            "regions": [
                {"key": f"r{i}", "bbox": [i, 0, i + 1, 1], "section": "Fixture"} for i in range(17)
            ],
        }
    ]
    with pytest.raises(ValidationError, match="16 reviewed"):
        OCRPlan.model_validate(data)
