"""Bounded region-only Tesseract fallback; original native evidence remains separate."""

import csv
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from io import StringIO
from pathlib import Path
from time import monotonic

from dietary_rag.corpus.contracts import ContentBlock, DocumentVersion, SourceLocation
from dietary_rag.corpus.identity import sha256_bytes, stable_id
from dietary_rag.corpus.registry import LOCAL_TEXT_RIGHTS
from dietary_rag.ingestion.ocr_contracts import (
    OCRCandidate,
    OCRExtraction,
    OCRImage,
    OCRPageClassification,
    OCRPlan,
    OCRWord,
)

ADAPTER_VERSION = "ocr_regions_v1"
MAX_PIXELS = 20_000_000
MAX_BYTES = 50_000_000
MAX_TOTAL_PIXELS = 120_000_000
MAX_SECONDS = 120


class OCRExtractionError(ValueError):
    """OCR routing, provenance or resource validation failed."""


def _intersection(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def _good_text(text):
    return bool(text) and sum(c.isprintable() and c != "\ufffd" for c in text) / len(text) >= 0.95


def _critical(text):
    return bool(
        re.search(
            r"\d|[°º%]|\b(?:f|c|mg|mcg|kg|grams?|minutes?|hours?|days?|fahrenheit|celsius)\b",
            text,
            re.I,
        )
    )


def _original_box(box, image):
    """Invert deskew then quarter-turn correction, crop offset and raster scaling."""
    w, h = image.original_pixel_size
    left, top, right, bottom = image.crop_pixel_box
    cw, ch = right - left, bottom - top
    rw, rh = (ch, cw) if image.rotation in (90, 270) else (cw, ch)
    angle = math.radians(-image.skew_degrees)
    c, s = math.cos(angle), math.sin(angle)

    def back(x, y):
        dx, dy = x - rw / 2, y - rh / 2
        x, y = rw / 2 + c * dx - s * dy, rh / 2 + s * dx + c * dy
        if image.rotation == 90:
            x, y = cw - y, x
        elif image.rotation == 180:
            x, y = cw - x, ch - y
        elif image.rotation == 270:
            x, y = y, ch - x
        return max(0, min(cw, x)) + left, max(0, min(ch, y)) + top

    corners = [back(x, y) for x in (box[0], box[2]) for y in (box[1], box[3])]
    # Actual rendered pixel dimensions account for renderer rounding, not just nominal DPI.
    sx, sy = image.original_page_size[0] / w, image.original_page_size[1] / h
    return (
        min(p[0] for p in corners) * sx,
        min(p[1] for p in corners) * sy,
        max(p[0] for p in corners) * sx,
        max(p[1] for p in corners) * sy,
    )


class TesseractEngine:
    def __init__(self, language="eng", executable=None):
        executable = executable or shutil.which("tesseract")
        if executable is None and Path("/opt/homebrew/bin/tesseract").exists():
            executable = "/opt/homebrew/bin/tesseract"
        if executable is None:
            raise OCRExtractionError("Tesseract is unavailable; install it or pass --tesseract")
        self.executable = str(Path(executable).resolve())
        self.language = language
        self.environment = {**os.environ, "OMP_THREAD_LIMIT": "1"}
        try:
            version = subprocess.run(
                [self.executable, "--version"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
                env=self.environment,
            )
            languages = subprocess.run(
                [self.executable, "--list-langs"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
                env=self.environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise OCRExtractionError("Tesseract inspection failed") from exc
        listing = languages.stdout + languages.stderr
        match = re.search(r'in "([^"]+)"', listing)
        if match is None or not set(language.split("+")) <= set(listing.splitlines()):
            raise OCRExtractionError("requested OCR language data unavailable")
        root = Path(match.group(1))
        self.metadata = {
            "name": "tesseract",
            "version": version.stdout.strip(),
            "binary_sha256": sha256_bytes(Path(self.executable).read_bytes()),
            "language": language,
            **{
                f"{lang}_traineddata_sha256": sha256_bytes(
                    (root / (lang + ".traineddata")).read_bytes()
                )
                for lang in language.split("+")
            },
        }

    def recognize(self, image_path, plan):
        try:
            with tempfile.TemporaryFile() as output:
                subprocess.run(
                    [
                        self.executable,
                        str(image_path),
                        "stdout",
                        "-l",
                        self.language,
                        "--psm",
                        str(plan.page_segmentation_mode),
                        "--dpi",
                        str(plan.dpi),
                        "tsv",
                    ],
                    stdout=output,
                    stderr=subprocess.PIPE,
                    check=True,
                    timeout=plan.timeout_seconds,
                    env=self.environment,
                )
                if output.tell() > 5_000_000:
                    raise OCRExtractionError("OCR TSV exceeds five-million-byte bound")
                output.seek(0)
                tsv = output.read().decode("utf-8")
        except (OSError, subprocess.SubprocessError) as exc:
            raise OCRExtractionError("bounded Tesseract run failed or timed out") from exc
        return list(csv.DictReader(StringIO(tsv), delimiter="\t"))


class OCRExtractor:
    def __init__(self, plan: OCRPlan, *, engine=None, tesseract=None):
        self.plan, self.engine, self.tesseract = plan, engine, tesseract

    def extract(
        self,
        raw_path: Path,
        document: DocumentVersion,
        *,
        output_dir=Path("data/staging/ocr-extraction"),
    ):
        result = self.extract_report(raw_path, document, output_dir=output_dir)
        if result.review_state == "quarantined":
            raise OCRExtractionError(
                "OCR includes unresolved/quarantined content; inspect extract_report"
            )
        return result.blocks

    def extract_report(self, raw_path, document, *, output_dir=Path("data/staging/ocr-extraction")):
        import pdfplumber
        import pypdf
        import pypdfium2
        from PIL import Image, ImageStat
        from PIL import __version__ as pillow_version

        plan = self.plan
        deadline = monotonic() + MAX_SECONDS
        rendered_pixels = 0
        native_word_total = 0
        if Path(raw_path).stat().st_size > MAX_BYTES:
            raise OCRExtractionError("OCR input exceeds 50 MB")
        raw = Path(raw_path).read_bytes()
        if (
            document.format != "pdf"
            or not raw.startswith(b"%PDF-")
            or sha256_bytes(raw) != document.raw_sha256
            or plan.raw_sha256 != document.raw_sha256
            or plan.document_version_id != document.document_version_id
        ):
            raise OCRExtractionError("OCR file/plan differs from original document provenance")
        if (
            document.eligibility.decision != "approved_with_exclusions"
            or not document.eligibility.default_index
            or document.content_role != "consumer_guidance"
            or document.rights_review.status not in LOCAL_TEXT_RIGHTS | {"project_created"}
        ):
            raise OCRExtractionError(
                "OCR requires approved default consumer text or a project-created fixture"
            )
        if (
            plan.fixture_kind != "original_pdf"
            and document.rights_review.status != "project_created"
        ):
            # Source-derived scans require an explicitly created synthetic document version/rights record.
            raise OCRExtractionError("synthetic OCR fixtures require project-created provenance")
        reader = pypdf.PdfReader(raw_path, strict=True)
        if (
            reader.is_encrypted
            or len(reader.pages) != document.page_count
            or document.page_count > 100
        ):
            raise OCRExtractionError("OCR PDF is encrypted or page count differs/exceeds 100")
        page_plans = {p.physical_page: p for p in plan.pages}
        if any(p > document.page_count for p in page_plans):
            raise OCRExtractionError("OCR plan refers to absent physical page")
        engine = self.engine  # Initialized only when a nonblank reviewed region actually needs OCR.
        pages, images, words, candidates, blocks, diagnostics = [], [], [], [], [], []
        quarantine = False
        config_data = {
            "adapter": ADAPTER_VERSION,
            "plan": plan,
            "pdfplumber": pdfplumber.__version__,
            "pypdf": pypdf.__version__,
            "pdfium": str(pypdfium2.PYPDFIUM_INFO),
            "pillow": pillow_version,
        }
        output_dir = Path(output_dir)
        with pdfplumber.open(raw_path) as pdf, pypdfium2.PdfDocument(raw_path) as renderer:

            def render(page_index, width, height, scale):
                nonlocal rendered_pixels
                pixels = math.ceil(width * scale) * math.ceil(height * scale)
                rendered_pixels += pixels
                if pixels > MAX_PIXELS or rendered_pixels > MAX_TOTAL_PIXELS:
                    raise OCRExtractionError("OCR rendering exceeds per-page or total pixel bound")
                rp = renderer[page_index]
                try:
                    bitmap = rp.render(scale=scale)
                    try:
                        return bitmap.to_pil().convert("RGB")
                    finally:
                        bitmap.close()
                finally:
                    rp.close()

            for pn, page in enumerate(pdf.pages, 1):
                if monotonic() >= deadline:
                    raise OCRExtractionError("OCR invocation exceeded 120-second budget")
                native = page.dedupe_chars().extract_words()
                native_word_total += len(native)
                if native_word_total > 100000 or len(page.images) > 1000:
                    raise OCRExtractionError("OCR classification exceeds native-word/image bounds")
                if any(
                    i.get("srcsize", (0, 0))[0] * i.get("srcsize", (0, 0))[1] > 50_000_000
                    for i in page.images
                ):
                    raise OCRExtractionError("embedded raster exceeds 50-million-pixel bound")
                good = [w for w in native if _good_text(w["text"])]
                quality = len(good) / len(native) if native else 0
                image_boxes = [
                    (
                        max(0, i["x0"]),
                        max(0, i["top"]),
                        min(page.width, i["x1"]),
                        min(page.height, i["bottom"]),
                    )
                    for i in page.images
                ]
                fraction = min(
                    1,
                    sum(_intersection(b, (0, 0, page.width, page.height)) for b in image_boxes)
                    / (page.width * page.height),
                )
                pp = page_plans.get(pn)
                regions = pp.regions if pp else ()
                if native and quality >= 0.9:
                    classification = (
                        "mixed"
                        if fraction >= 0.15
                        else ("native" if len(good) >= 12 else "sparse_native")
                    )
                elif page.images:
                    classification = "mixed" if native else "scanned"
                elif not native and not page.curves and not page.rects and not page.lines:
                    classification = "blank"
                else:
                    classification = "visual_only" if not native else "sparse_native"
                rendered = None
                render_path = None
                if (not native and page.images) or (pp and pp.intentional_blank):
                    thumbnail = render(pn - 1, page.width, page.height, 1)
                    gray = thumbnail.convert("L")
                    stats = ImageStat.Stat(gray)
                    minimum_pixel = gray.getextrema()[0]
                    gray.close()
                    thumbnail.close()
                    # A sparse/faint mark is content, not proof of a blank scan.
                    if not native and minimum_pixel >= 250 and stats.stddev[0] < 1:
                        classification = "blank"
                if pp and pp.intentional_blank and classification != "blank":
                    raise OCRExtractionError(
                        "intentional-blank override contains native/visible content"
                    )
                for idx, region in enumerate(regions):
                    if region.bbox[2] > page.width or region.bbox[3] > page.height:
                        raise OCRExtractionError("reviewed OCR region exceeds original page")
                    if any(_intersection(region.bbox, prior.bbox) > 0 for prior in regions[:idx]):
                        raise OCRExtractionError("reviewed OCR regions overlap")
                    if any(
                        _intersection(region.bbox, (w["x0"], w["top"], w["x1"], w["bottom"])) > 0
                        for w in good
                    ):
                        raise OCRExtractionError(
                            "OCR region overlaps selectable native text; duplication forbidden"
                        )
                    if not any(_intersection(region.bbox, b) > 0 for b in image_boxes):
                        raise OCRExtractionError(
                            "OCR region has no raster content; review vector/native extraction"
                        )
                route = (
                    "skip_blank"
                    if classification == "blank"
                    else "ocr_regions"
                    if regions
                    else "native_only"
                    if classification in ("native", "sparse_native") and quality >= 0.9
                    else "needs_review"
                )
                if route == "needs_review":
                    quarantine = True
                    diagnostics.append(
                        f"Page {pn}: image/poor-native/visual content requires a reviewed region plan; no OCR invented."
                    )
                if regions and classification == "blank":
                    diagnostics.append(f"Page {pn}: blank raster verified; OCR skipped.")
                pages.append(
                    OCRPageClassification(
                        physical_page=pn,
                        width=page.width,
                        height=page.height,
                        rotation=int(reader.pages[pn - 1].get("/Rotate", 0)),
                        native_word_count=len(native),
                        native_quality=quality,
                        image_area_fraction=fraction,
                        classification=classification,
                        route=route,
                        reason=f"{len(good)} usable native words; {len(page.images)} raster images; reviewed regions={len(regions)}",
                    )
                )
                if route != "ocr_regions":
                    if rendered is not None:
                        rendered.close()
                    continue
                if engine is None:
                    engine = TesseractEngine(plan.language, self.tesseract)
                rendered = render(pn - 1, page.width, page.height, plan.dpi / 72)
                config = stable_id("ocr_config", {**config_data, "engine": engine.metadata})
                dest = output_dir / config
                dest.mkdir(parents=True, exist_ok=True)
                render_path = dest / f"page-{pn:03d}-original.png"
                rendered.save(render_path)
                for region in regions:
                    pw, ph = rendered.size
                    box = (
                        math.floor(region.bbox[0] * pw / page.width),
                        math.floor(region.bbox[1] * ph / page.height),
                        math.ceil(region.bbox[2] * pw / page.width),
                        math.ceil(region.bbox[3] * ph / page.height),
                    )
                    crop = rendered.crop(box)
                    corrected = crop.rotate(region.rotation, expand=True, fillcolor="white")
                    crop.close()
                    if region.skew_degrees:
                        turned = corrected.rotate(
                            -region.skew_degrees,
                            resample=Image.Resampling.BICUBIC,
                            expand=False,
                            fillcolor="white",
                        )
                        corrected.close()
                        corrected = turned
                    processed = dest / f"page-{pn:03d}-{region.key}-processed.png"
                    corrected.save(processed)
                    image = OCRImage(
                        image_id=stable_id(
                            "ocr_image",
                            {
                                "config": config,
                                "page": pn,
                                "region": region,
                                "processed": sha256_bytes(processed.read_bytes()),
                            },
                        ),
                        physical_page=pn,
                        region_key=region.key,
                        original_page_png_sha256=sha256_bytes(render_path.read_bytes()),
                        processed_png_sha256=sha256_bytes(processed.read_bytes()),
                        original_pixel_size=rendered.size,
                        original_page_size=(page.width, page.height),
                        processed_pixel_size=corrected.size,
                        crop_pixel_box=box,
                        region_bbox=region.bbox,
                        rotation=region.rotation,
                        skew_degrees=region.skew_degrees,
                        dpi=plan.dpi,
                    )
                    corrected.close()
                    images.append(image)
                    try:
                        remaining = int(deadline - monotonic())
                        if remaining < 1:
                            raise OCRExtractionError("OCR invocation exceeded 120-second budget")
                        rows = engine.recognize(
                            processed,
                            plan.model_copy(
                                update={"timeout_seconds": min(plan.timeout_seconds, remaining)}
                            ),
                        )
                    except OCRExtractionError as exc:
                        quarantine = True
                        diagnostics.append(f"Page {pn}, {region.key}: {exc}")
                        continue
                    if len(rows) > 20000 or len(words) + len(rows) > 50000:
                        raise OCRExtractionError("OCR exceeds bounded word count")
                    groups = defaultdict(list)
                    for idx, row in enumerate(rows):
                        text = row.get("text", "").strip()
                        if not text:
                            continue
                        try:
                            conf = float(row["conf"])
                            x, y = float(row["left"]), float(row["top"])
                            bw, bh = float(row["width"]), float(row["height"])
                            if (
                                not all(math.isfinite(v) for v in (conf, x, y, bw, bh))
                                or not 0 <= conf <= 100
                                or min(x, y) < 0
                                or min(bw, bh) <= 0
                                or x + bw > image.processed_pixel_size[0]
                                or y + bh > image.processed_pixel_size[1]
                            ):
                                raise ValueError("invalid TSV geometry/confidence")
                            line = tuple(int(row[k]) for k in ("block_num", "par_num", "line_num"))
                        except (ValueError, KeyError) as exc:
                            raise OCRExtractionError("invalid OCR engine output") from exc
                        loc = SourceLocation(
                            format="pdf",
                            physical_page=pn,
                            printed_page_label=pp.printed_page_label,
                            bbox=_original_box((x, y, x + bw, y + bh), image),
                            extraction_method="ocr",
                            ocr_confidence=conf,
                        )
                        word = OCRWord(
                            word_id=stable_id(
                                "ocr_word", {"image": image.image_id, "index": idx, "row": row}
                            ),
                            image_id=image.image_id,
                            text=text,
                            confidence=conf,
                            location=loc,
                            processed_bbox=(x, y, x + bw, y + bh),
                            line_key=line,
                            critical=_critical(text),
                        )
                        words.append(word)
                        groups[line[:2]].append(word)
                    if not groups:
                        quarantine = True
                        diagnostics.append(
                            f"Page {pn}, {region.key}: no recognized words; region quarantined."
                        )
                    for group in groups.values():
                        text = " ".join(w.text for w in group)
                        reasons = []
                        if min(w.confidence for w in group) < plan.minimum_confidence:
                            reasons.append("low_word_confidence")
                        if any(
                            w.critical and w.confidence < plan.critical_confidence for w in group
                        ):
                            reasons.append("uncertain_numeric_or_unit")
                        if any(not _good_text(w.text) for w in group) or re.search(
                            r"\b\d+[OoIl]\b|\b[OoIl]+\d+\b", text
                        ):
                            reasons.append("ambiguous_character_or_numeric_token")
                        if region.kind == "table":
                            reasons.append("ocr_table_requires_cell_and_header_review")
                        if region.skew_degrees and any(
                            w.processed_bbox[0] < 2
                            or w.processed_bbox[1] < 2
                            or w.processed_bbox[2] > image.processed_pixel_size[0] - 2
                            or w.processed_bbox[3] > image.processed_pixel_size[1] - 2
                            for w in group
                        ):
                            reasons.append("deskew_edge_clipping")
                        state = "quarantined" if reasons else "pending"
                        boxes = [w.location.bbox for w in group]
                        loc = SourceLocation(
                            format="pdf",
                            physical_page=pn,
                            printed_page_label=pp.printed_page_label,
                            bbox=(
                                min(b[0] for b in boxes),
                                min(b[1] for b in boxes),
                                max(b[2] for b in boxes),
                                max(b[3] for b in boxes),
                            ),
                            extraction_method="ocr",
                            ocr_confidence=min(w.confidence for w in group),
                            review_state=state,
                        )
                        block = None
                        if not reasons:
                            block = ContentBlock(
                                block_id=stable_id(
                                    "ocr_block",
                                    {
                                        "config": config,
                                        "image": image.image_id,
                                        "text": text,
                                        "loc": loc,
                                    },
                                ),
                                document_version_id=document.document_version_id,
                                block_type="paragraph",
                                heading_path=(document.document_name, region.section),
                                sequence_index=len(blocks),
                                text=text,
                                source_locations=(loc,),
                            )
                            blocks.append(block)
                        else:
                            quarantine = True
                        candidates.append(
                            OCRCandidate(
                                candidate_id=stable_id(
                                    "ocr_candidate",
                                    {"image": image.image_id, "words": group, "reasons": reasons},
                                ),
                                image_id=image.image_id,
                                text=text,
                                word_ids=tuple(w.word_id for w in group),
                                source_location=loc,
                                state=state,
                                reasons=tuple(reasons),
                                block_id=block.block_id if block else None,
                            )
                        )
                rendered.close()
        config = stable_id(
            "ocr_config", {**config_data, "engine": engine.metadata if engine else {}}
        )
        payload = dict(
            configuration_id=config,
            adapter_version=ADAPTER_VERSION,
            document=document,
            fixture_kind=plan.fixture_kind,
            engine=engine.metadata if engine else {},
            pages=tuple(pages),
            images=tuple(images),
            words=tuple(words),
            candidates=tuple(candidates),
            blocks=tuple(blocks),
            diagnostics=tuple(diagnostics),
            review_state="quarantined" if quarantine else "pending",
        )
        return OCRExtraction(extraction_id=stable_id("ocr_extraction", payload), **payload)
