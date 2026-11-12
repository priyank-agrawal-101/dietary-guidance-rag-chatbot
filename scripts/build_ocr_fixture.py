"""Create one project-owned mixed native/scanned fixture; no official artwork copied."""

import argparse
import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from dietary_rag.corpus.contracts import DocumentVersion, SourceEligibility
from dietary_rag.corpus.identity import sha256_bytes
from dietary_rag.ingestion.ocr_contracts import OCRPagePlan, OCRPlan, OCRRegion


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("tests/fixtures/ocr"))
    parser.add_argument(
        "--font", type=Path, default=Path("/System/Library/Fonts/Supplemental/Arial.ttf")
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / "project-mixed-scan.pdf"
    c = canvas.Canvas(str(path), pagesize=(600, 800), invariant=1, pageCompression=1)

    def raster(lines, rotation=0, skew=0, degraded=False):
        image = Image.new("RGB", (1600, 450), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(args.font), 64 if not degraded else 36)
        for i, line in enumerate(lines):
            draw.text(
                (70, 70 + i * 100),
                line,
                font=font,
                fill=(0, 0, 0) if not degraded else (160, 160, 160),
            )
        if skew:
            image = image.rotate(
                skew, resample=Image.Resampling.BICUBIC, expand=False, fillcolor="white"
            )
        if rotation:
            image = image.rotate(rotation, expand=True, fillcolor="white")
        if degraded:
            image = image.filter(ImageFilter.GaussianBlur(3))
        return image

    def embed(image, x=40, y=420, width=520, height=146.25):
        memory = BytesIO()
        image.save(memory, format="PNG")
        memory.seek(0)
        c.drawImage(ImageReader(memory), x, y, width=width, height=height)
        image.close()

    c.setFont("Helvetica", 14)
    c.drawString(
        40, 700, "Native page: keep food clean and separate raw foods from ready to eat foods."
    )
    c.showPage()
    c.showPage()  # Intentional blank.
    c.setFont("Helvetica", 20)
    c.drawString(40, 700, "Sparse native cover")
    c.showPage()
    embed(raster(["Keep food chilled.", "Use clean utensils."]))
    footer = Image.new("RGB", (120, 80), "white")
    ImageDraw.Draw(footer).text(
        (45, 10), "7", font=ImageFont.truetype(str(args.font), 40), fill="black"
    )
    embed(footer, x=285, y=30, width=30, height=20)
    c.showPage()
    c.setFont("Helvetica", 14)
    c.drawString(40, 740, "Selectable native heading remains separate.")
    embed(raster(["Wash hands before preparing food.", "Keep utensils clean."]))
    c.showPage()
    embed(
        raster(["Separate raw and cooked foods."], rotation=-90),
        x=200,
        y=130,
        width=146.25,
        height=520,
    )
    c.showPage()
    embed(raster(["Keep food chilled.", "Use clean utensils."], skew=2))
    c.showPage()
    embed(raster(["Store at 40 F for 2 hours."], degraded=True))
    c.showPage()
    embed(Image.new("RGB", (1600, 450), "white"))
    c.showPage()
    c.save()
    document = DocumentVersion(
        document_id="project_mixed_scan",
        document_version_id="docv_project_mixed_scan",
        family_id="project_ocr_fixture",
        document_name="Project-created mixed OCR fixture",
        publisher="Project",
        source_url="https://example.gov/project-mixed-scan.pdf",
        format="pdf",
        retrieved_at=datetime(2026, 11, 12, tzinfo=UTC),
        source_tier="official_guidance",
        jurisdiction="US",
        target_audience="US",
        intended_audience="household consumers",
        content_role="consumer_guidance",
        eligibility=SourceEligibility(
            decision="approved_with_exclusions",
            default_index=True,
            eligible_sections=("Project fixture instructions",),
        ),
        rights_review={
            "status": "project_created",
            "scope": "Project-owned synthetic OCR test sentences; no official source images.",
        },
        raw_sha256=sha256_bytes(path.read_bytes()),
        page_count=9,
        text_classification="mixed",
    )

    def region(key="instructions", bbox=(40, 233.75, 560, 380), **kw):
        return OCRRegion(key=key, bbox=bbox, section="Project fixture instructions", **kw)

    plan = OCRPlan(
        raw_sha256=document.raw_sha256,
        document_version_id=document.document_version_id,
        fixture_kind="project_created_scan",
        review_note="Synthetic project-owned fixture, not a naturally scanned official publication. Pages 2/9 blank, 3 sparse cover; image instructions have explicit reading rectangles. Page 8 deliberately degraded numeric/unit content.",
        pages=(
            OCRPagePlan(physical_page=2, intentional_blank=True),
            OCRPagePlan(physical_page=4, printed_page_label="7", regions=(region(),)),
            OCRPagePlan(physical_page=5, regions=(region(),)),
            OCRPagePlan(
                physical_page=6, regions=(region(bbox=(200, 150, 346.25, 670), rotation=90),)
            ),
            OCRPagePlan(physical_page=7, regions=(region(skew_degrees=2),)),
            OCRPagePlan(physical_page=8, regions=(region(),)),
            OCRPagePlan(physical_page=9, intentional_blank=True),
        ),
    )
    (args.output_dir / "document.json").write_text(document.model_dump_json(indent=2) + "\n")
    (args.output_dir / "plan.json").write_text(plan.model_dump_json(indent=2) + "\n")
    manifest = {
        "fixture_kind": plan.fixture_kind,
        "fixture_sha256": document.raw_sha256,
        "description": plan.review_note,
        "page_kinds": [
            "native",
            "blank",
            "sparse_native",
            "scanned",
            "mixed",
            "scanned_rotated",
            "scanned_skewed",
            "scanned_degraded_numeric",
            "blank_raster",
        ],
        "expected_good_text": {
            "4": ["Keep food chilled.", "Use clean utensils."],
            "5": ["Wash hands before preparing food.", "Keep utensils clean."],
            "6": ["Separate raw and cooked foods."],
            "7": ["Keep food chilled.", "Use clean utensils."],
        },
        "degraded_expected_text": "Store at 40 F for 2 hours.",
        "generator_font_sha256": sha256_bytes(args.font.read_bytes()),
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
