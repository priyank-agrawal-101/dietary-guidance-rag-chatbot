# Phase 2.3: OCR fallback

**Current downstream status:** The extraction-stage results below record this phase's scope and review state at completion. Shared normalization and [structure-aware chunking](phase2-chunking.md) are now implemented, and the Phase 2 ingestion development gate has passed. Native outputs still require exact downstream review receipts; uncertain OCR remains quarantined. Vector indexing and activation remain planned.

**Completed November 12, 2026: P2O.1–P2O.3.** A separate OCR adapter classifies pages, renders only reviewed image regions for recognition, preserves original geometry/confidence and quarantines uncertain candidates. The project-created nine-page fixture exercises native text, a sparse cover, two kinds of blank pages, scans, a mixed page, rotation, skew and deliberately degraded numeric/unit text. Real Tesseract produces **four matching pending blocks and one quarantined candidate** from five OCR regions. See the [review record](ocr-extraction-review.json) and [original/corrected comparison](examples/ocr-review/original-and-corrected.png).

## Simple explanation

Native extraction reads letters already stored in a PDF. OCR reads letters from a picture. This adapter first checks which kind of content a page contains. It leaves selectable native text to the existing PDF extractor and does not OCR an empty page or a sparse text cover just because there are few words.

A mixed page can have a selectable heading and an image containing instructions. A reviewed rectangle selects the image instructions. Any rectangle overlapping usable native text is rejected, preventing duplicate text. Images outside reviewed rectangles remain outside OCR; the adapter does not decide that every photograph or illustration needs interpretation.

OCR can confuse numbers, punctuation and units. The fixture's clear instructions match the original project-authored text. Its blurred sentence originally says `Store at 40 F for 2 hours.` Tesseract reads the words and numbers but drops the final period, with insufficient confidence in the numeric/unit content. That entire candidate is quarantined: it remains in the administrative report and produces **no retained block**. Confidence is an engine score, not a guarantee that the text is correct.

Each recognized word points to the original PDF, physical page, page-image hash and word rectangle. Correcting a sideways or skewed image changes the working picture; inverse coordinate transforms still place the word box on the original page. Printed labels come only from the explicit reviewed plan: fixture physical page 4 visibly prints `7` in a separate raster footer.

## Run it

The Python OCR extra reuses native PDF dependencies. Tesseract and its language data are separate system dependencies:

```sh
python -m pip install --require-hashes -r requirements/ocr.lock
# macOS; install only when Tesseract is absent:
brew install tesseract
# Debian/Ubuntu alternative:
# sudo apt-get install tesseract-ocr tesseract-ocr-eng

python -m dietary_rag extract-ocr \
  --file tests/fixtures/ocr/project-mixed-scan.pdf \
  --document tests/fixtures/ocr/document.json \
  --plan tests/fixtures/ocr/plan.json \
  --report data/staging/ocr-extraction/report.json
```

The example intentionally returns **exit code 1**, because the blurred candidate is quarantined. Exit code `0` means the OCR result passed automatic quality checks and remains pending human/downstream review; `2` means invalid inputs/configuration or an unavailable engine. A timeout during recognition quarantines that region and appears in the extraction diagnostics.

`--document` is an original `DocumentVersion` JSON; `--plan` is an `OCRPlan` bound to its version ID and exact raw SHA-256. `--output-dir`, `--config` and `--project-root` follow the existing CLI conventions. `--tesseract /absolute/path/to/tesseract` selects an executable explicitly. The default discovers PATH or the usual macOS Homebrew location.

The command writes an extraction JSON and deterministic original-page/processed-region PNGs beneath its configuration ID. It never modifies the acquisition catalog, existing native exports, source approvals or an active corpus. Repeating unchanged inputs with the same engine/configuration produces identical identities and contents.

For actual reviewed sources, use `fixture_kind: original_pdf`, the verified acquired file and its document-version metadata. The CLI verifies current source eligibility, byte review and metadata before OCR. Plans must explicitly document retained image regions and exclusion review. There are no automatic OCR region plans for the currently inspected official PDFs; their native text remains handled by P2P. Synthetic/project-created fixtures are labeled separately and do not count as additional official acquisitions or searchable consumer families.

## Technical foundations

The implementation lives in [ocr.py](../src/dietary_rag/ingestion/ocr.py), [ocr_contracts.py](../src/dietary_rag/ingestion/ocr_contracts.py) and the `extract-ocr` CLI branch.

1. **Classification:** pdfplumber exposes native word presence, usable-text ratio and raster bounds. Pages are classified as `native`, `sparse_native`, `blank`, `scanned`, `mixed` or `visual_only`. The usable-text heuristic checks printable characters/replacement characters; it does not prove linguistic correctness. A 72-DPI thumbnail verifies image-only blank pages using darkness and pixel variation. An intentional-blank override containing visible/native content fails. Unplanned image/poor-native/visual content receives `needs_review`, with no speculative OCR.
2. **Reviewed routing:** `OCRPlan` carries original-byte/version identity, fixture kind, review note, page labels and ordered regions. Regions must stay inside the original page, reference raster content, avoid each other and avoid usable native words. OCR output stays separate from native blocks; later shared processing can combine reviewed evidence without appending duplicate text.
3. **Rendering and corrections:** PDFium renders reviewed pages at 300 DPI by default; allowed DPI is 150–400. Recognition receives only the cropped image region. Explicit counterclockwise quarter-turn corrections and skew corrections within ±5 degrees are supported. Skew uses inverse rotation with a fixed canvas; possible edge clipping quarantines affected text. Orientation/skew values are reviewed inputs, not automatic guesses. Original and processed PNG hashes, dimensions, crop offsets and corrections are recorded.
4. **Engine provenance:** the adapter invokes the Tesseract executable directly with argument lists, TSV output, language and page-segmentation mode. No shell command or Python OCR wrapper is needed. It records the full version report, executable hash and each language-traineddata hash. This run uses **Tesseract 5.5.4**, Leptonica 1.87.0 and English data. Python package versions and plan/engine metadata feed configuration identity. Tesseract is Apache-2.0 licensed and Leptonica uses a BSD-style license; system binaries/language data are recorded per run rather than claimed to be pinned by the Python lock.
5. **Geometry and quality:** TSV words retain confidence, processed boxes and paragraph/line keys. Inverse deskew/quarter-turn transforms, crop offsets and actual raster scale map all four box corners back to original top-left PDF points. Candidates group recognized paragraphs. Any word below 85 confidence quarantines its candidate; numeric/unit words below 95, ambiguous numeric characters, bad text or possible skew clipping also quarantine it. Thresholds can be raised, but cannot be weakened below these floors. OCR tables are always held as candidates until cell/header/condition review; they are never flattened into retained table prose.
6. **Contracts and resource bounds:** `OCRExtraction` validates physical-page coverage, unique IDs, image/word/candidate relationships, geometry, block order and the confidence gate. A quarantined candidate cannot refer to a retained block. Parsing is bounded at 50 MB, 100 pages, 100,000 native words, 1,000 images per page and 50 million pixels per embedded raster. There are at most 16 OCR regions, 20 million pixels per render, 120 million rendered pixels per invocation, 20,000 TSV rows per region and 50,000 total OCR rows/words. Recognition has a 1–30-second timeout per call and shares a 120-second invocation budget. Tesseract uses one OpenMP thread; TSV is captured to a temporary file and bounded at five million bytes before loading into memory. PDF parsing/rendering calls themselves are synchronous; the deadline is checked between operations, so it is not a hard operating-system kill timer for the PDF libraries.

`OCRExtractor.extract_report()` exposes all classifications, images, words, candidates, retained blocks and diagnostics. `extract()` implements the shared block interface and raises for a quarantined result. Quarantined words/candidates are administrative review artifacts and must never be substituted for retained blocks. Even passing blocks remain `pending`; this adapter offers no automatic promotion to `reviewed` or corpus activation.

## Fixture and validation

The [fixture manifest](../tests/fixtures/ocr/manifest.json) labels the PDF as **project-created**, never as a naturally scanned official publication. The [builder](../scripts/build_ocr_fixture.py) generates original short test sentences and rasterizes them with a locally supplied font; no government artwork or scanned source image is copied. The font hash is recorded for rebuild transparency, and the font itself is not distributed. The committed fixture makes normal tests portable; rebuilding accepts `--font` for another installation.

| Physical page | Content | Routing / observed result |
| ---: | --- | --- |
| 1 | Selectable sentence | Native only; no OCR. |
| 2 | Intentionally blank | Verified blank; no OCR. |
| 3 | Sparse native cover | Native only; no OCR. |
| 4 | Clear raster instructions, printed label 7 | One pending OCR block; original words match. |
| 5 | Native heading plus raster instructions | OCR only in the image rectangle; native heading not duplicated. |
| 6 | Sideways raster instructions | 90-degree correction; text and inverse word boxes verified. |
| 7 | Skewed raster instructions | Two-degree correction; text and inverse word boxes verified. |
| 8 | Blurred temperature/time sentence | Numeric/unit confidence fails; candidate quarantined, no block. |
| 9 | White raster image | Thumbnail identifies blank; no OCR. |

All nine latest fixture pages and five original/corrected image regions were visually inspected. The review JSON records the four exact text comparisons, the degraded candidate and source-image hashes. Unit tests use deterministic TSV to exercise confidence thresholds and failure paths quickly. Integration tests invoke real Tesseract and verify repeated CLI output; they explicitly skip when the system executable is absent rather than falsely claiming real OCR coverage.

Verification on Python 3.13: **224 tests pass, including 36 OCR checks**; Ruff lint/format, hash-lock installation, dependency consistency, registry validation and wheel build pass. The complete suite takes about 21 seconds and the real fixture command under one second on this machine. Tests cover routing, mixed-page duplication protection, all inverse quarter-turns, crop/deskew geometry, labels, missing languages/engine, malformed TSV, timeout quarantine, region/provenance/resource validation and uncertain numbers/units/table content.

This completes the isolated P2O quality checks using existing shared `ContentBlock`/`SourceLocation` contracts. At this P2O checkpoint, shared normalization, chunking and activation were still pending. P2N/P2C are now complete as recorded above; activation remains planned. The plan's P2N.1 dependency remains necessary before combining these OCR blocks into a normalized consumer corpus. Diagram/photo interpretation and OCR table reconstruction are not demonstrated by this prose fixture. OCR completion does not close the full Phase 2 exit gate.
