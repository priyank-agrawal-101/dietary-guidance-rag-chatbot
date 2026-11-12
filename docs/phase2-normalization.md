# Phase 2.4: shared normalization and exclusions

**Completed November 12, 2026: P2N.1–P2N.3.** The shared pipeline produces **443 canonical blocks and three tables from the 16 selected assets**. Three approved comparison PDFs add 54 blocks and two tables, for **497 blocks, five tables and 250 retained table values compared**. The project-created OCR fixture contributes four passing blocks to a separate quarantined result; its uncertain temperature/time candidate remains unavailable. Comparisons and synthetic fixtures do not increase consumer-family counts.

The [review record](normalization-review.json) records source/native-artifact hashes, transformations, table comparisons, the NHLBI correction and exact review receipts. Native inputs remain intact. Reviewed canonical exports exist in ignored `data/staging/normalization`; structure-aware chunking is now implemented in [Phase 2.5](phase2-chunking.md). Indexing and activation remain later tasks.

## Simple explanation

HTML, native PDF and OCR initially produce different kinds of inspection data. This step turns their retained content into the same block structure, with the same rules for text, tables, source locations and quality status.

Cleaning must preserve meaning. A nonbreaking space can become an ordinary space; `ﬁsh` can become `fish`. But `½`, `40°F`, `2–3 days`, `don't freeze` and a three-minute rest instruction must survive. The normalizer uses Unicode NFC rather than a broad compatibility conversion that could rewrite fractions or symbols. Nested lists keep their indentation, and tables keep separate rows, categories, headers, values, blank cells and qualifying notes.

Hyphens need evidence. The sodium sheet wraps `vegeta-` onto a line containing `bles`; that becomes `vegetables`. A source word's position confirms the line break, and a reviewed spelling rule specifies the repair. True compounds such as `whole-grain` retain their hyphen. Suspended wording such as `low- or reduced-sodium` stays intact. Unknown splits are not guessed.

Exclusions operate on whole paragraphs, regions or tables. If a paragraph contains an excluded calculator, treatment, calorie/weight target, supplement dose or nutrient lookup, the whole block is removed rather than leaving a potentially misleading fragment. The omission record says why and links to its original location. Existing extraction masks still exclude adjacent callouts, worked labels, page furniture and visual/unlicensed media.

One real error was found during this review: the earlier sodium-sheet dining-card boundaries omitted words and mixed the fourth and fifth cards. The PDF reading regions were corrected and the original native text extracted again. This recovered words such as `Ask` and separated `Easy does it on the condiments` from `Go for healthy appetizers and side dishes`. It did not generate replacement guidance. See the [corrected region sample](examples/normalization-review/nhlbi-dining-regions.png); it uses the portable text-only fixture, while original-page renders remain in staging.

## Run it

No additional dependencies are needed: normalization consumes existing JSON and uses the core Python/Pydantic dependencies. It does not reparse HTML/PDF, run OCR, download sources or read/write the acquisition catalog.

Refresh native PDF exports first to include the corrected NHLBI profile:

```sh
python -m dietary_rag extract-pdf --all-eligible \
  --report data/staging/normalization-inputs/pdf-report.json
python -m dietary_rag normalize-extractions \
  --reports data/staging/html-extraction/report.json \
            data/staging/normalization-inputs/pdf-report.json \
  --report data/staging/normalization/report.json
```

Alternatively, pass explicit native extraction JSON filenames as positional arguments. `--reports` accepts explicitly named HTML/PDF/OCR CLI reports; it reads only their declared extraction artifacts, never scans a staging directory for arbitrary inputs. Select files **or** reports. The command supports 1–32 inputs, with a 20-MB limit per JSON artifact, plus the usual `--output-dir`, `--config`, `--project-root` and `--report` options.

Exit code `0` means all selected inputs normalized successfully, either pending or reviewed; `1` means an input failed or remained quarantined; `2` means invalid configuration/selection. Reports state each source's outcome, original input, output, counts, comparison flag and fixture kind. The OCR example intentionally returns `1`, because its unresolved candidate keeps that result quarantined even though the four passing blocks are preserved.

Normalization initially produces `pending` locations. `--review-file` applies exact `NormalizationReview` receipts; it does not approve arbitrary new output. The committed review bundle covers all 19 original default/comparison inputs used in this run:

```sh
python -m dietary_rag normalize-extractions \
  --reports data/staging/html-extraction/report.json \
            data/staging/normalization-inputs/pdf-report.json \
            data/staging/pdf-extraction/storage-comparison-report.json \
            data/staging/pdf-extraction/fda-handling-comparison-report.json \
            data/staging/pdf-extraction/fda-produce-comparison-report.json \
  --review-file docs/normalization-review.json \
  --report data/staging/normalization/reviewed-report.json
```

Those comparison reports require the existing approved original files/receipts described in [PDF extraction](phase2-pdf-extraction.md). For a smaller input selection, supply a receipt bundle containing only the corresponding reviews. Missing/changed acquisitions or changed input JSON/configuration require another exact review; the committed receipts are not portable approvals for reconstructed fixtures. Reviewed exports use a `-reviewed.json` suffix so pending exports remain inspectable.

## Technical foundations

The implementation is in [normalization.py](../src/dietary_rag/ingestion/normalization.py), [normalization_profiles.py](../src/dietary_rag/ingestion/normalization_profiles.py) and [normalization_contracts.py](../src/dietary_rag/ingestion/normalization_contracts.py).

1. **Typed adapters and original links:** `Normalizer.normalize()` accepts `HTMLExtraction`, `PDFExtraction` or `OCRExtraction`. It validates native extraction identity, reviewed source metadata/raw SHA-256, selection fingerprints and source profiles. Output `ContentBlock` records preserve document-version identity, order, headings and every original location. The native extraction ID and exact input-artifact SHA-256 link the canonical result back to its complete original evidence.
2. **Conservative text rules:** whitespace and a small ligature map are normalized; fractions, temperature symbols, range dashes, punctuation and footnote markers remain. Recommendation lists preserve nesting and line boundaries; table rows/footnotes keep line boundaries. Protected numeric, unit and condition tokens are compared before/after each transformation. A transformation that alters these tokens fails. This token check supplements exact table/source comparisons; it is not a general proof of semantic equivalence.
3. **Source masks:** reviewed HTML/PDF profiles remain the region/paragraph authority, now checked in the shared stage. Additional versioned rules cover calculator, treatment, calorie/weight, supplement-dose and individual-food nutrient lookup content. FDA label paragraphs also retain the reviewed allowlist. Nutrient lookup tables are excluded as whole tables. General age/nutrient-function context, label semantics and warnings such as `Color is not a reliable indicator of doneness` remain; keywords alone do not make those excluded treatment advice. Media is never copied into canonical evidence.
4. **Geometry and cell fidelity:** PDF prose words must belong to their declared reviewed region and must not come from the omitted-word set. Block text must match its word links. Table layouts cannot include excluded source-cell words; serialized text must match retained rows, headers and footnotes. Violations quarantine the affected block. Canonical `NormalizedTable` rows link to the original table ID and source row/page, preserving values, categories and blank cells. Original native artifacts retain span/grid/cell details for inspection; normalization does not consolidate different HTML/PDF layouts to make them appear identical.
5. **Inspectable decisions:** every original block gets a retained/excluded/quarantined decision with original locations and text hash. Changed fields get a before/after record and rule; reviewed line-wrap repairs also record the two native word IDs. Upstream DOM, paragraph, region, furniture, media and table omissions retain location/reason links. Raw excluded guidance and original words/cells remain in the native artifact rather than being republished as canonical blocks.
6. **Quality and review:** uncertain OCR and unresolved native tables remain quarantined. Passing OCR blocks keep original confidence/geometry and their synthetic/original label. Exact review receipts bind normalization/configuration/native-extraction IDs, raw hash and the complete ordered block-ID list. A matching receipt changes only quality status to `reviewed`; it cannot change content, locations or evidence identities, and cannot approve a quarantined result. Review scope is **transformation and source-specific exclusion fidelity**, not answer grounding or release readiness.
7. **Identities and validation:** configuration includes normalizer version, Unicode version, source profile, masks, repair rules and eligibility. Canonical block IDs bind configuration, original block, content and locations. Normalization identity also covers its audit records/input hash. Pydantic checks those identities, relationships, review receipts, table/block context, ordering and original locations, including when JSON is loaded again. Separate repeated source blocks cannot silently collapse into one.

All normalization imports remain free of optional parsers, OCR engines and model packages. Source-specific repairs require native PDF word geometry; OCR text receives no spelling or numeric “correction.” The later chunking stage should consume retained canonical blocks and structured rows, with the required review/eligibility gates, rather than diagnostics or raw comparison artifacts.

## Source review and verification

All **497 default/comparison blocks** preserve source locations and protected numeric/condition tokens. The 250 retained table values, categories, headers, context and qualifiers match native extraction outputs; empty values remain empty. Prior [HTML](html-extraction-review.json) and [PDF](pdf-extraction-review.json) original-cell comparisons establish their source correspondence. The current review supersedes the earlier NHLBI dining-region check: each of its five corrected cards was independently compared with `extract_text()` from a crop of the original page, and original/corrected rendered regions were visually checked.

The 16 default assets retain 443 blocks, 43 table data rows and 142 retained table values. Three comparison PDFs retain another 54 blocks, 40 rows and 108 values. The 12 default-source transformation records comprise one table-whitespace cleanup and 11 reviewed line-wrap repairs; numbers, conditions and qualifiers do not change. The HTML storage chart keeps its original four-column expanded span layout, including empty category/product cells; the PDF counterpart keeps three native columns. These alternate representations remain separately attributable.

NHLBI retains shopping, seasoning, preparation and all five dining cards, while its adjacent treatment callouts/footer and entire second-page sodium lookup stay excluded. HHS calculator, supplement and worked-label regions remain excluded, while child/adult audience metadata and allowed nutrient-function context survive. Tests specifically retain the general `energy needs` statement and safety negations, avoiding overbroad treatment/target filtering.

Verification on Python 3.13: **265 tests pass, including 41 normalization checks**. Ruff lint/format, dependency consistency, registry validation and wheel build pass. Real batch exports and exact receipt application are reproducible; CLI tests verify the catalog is untouched. Portable native fixtures cover all ten HTML and nine PDF layouts; additional cases exercise uncertain OCR, targets/treatment/dosing/lookups, FDA paragraph masks, invisible text protection, nested lists, numeric/unit/fraction/negation preservation, table/adjacent-word tampering, changed extraction IDs and exact receipt/location validation. The complete suite takes about 27 seconds on this machine; the actual normalization batch takes well under a second.

This closes P2N.1–P2N.3 and the shared-normalization dependency needed by OCR's consumer-ingestion path. The OCR fixture itself remains quarantined. Tokenizer-sized prose/table chunks, persistence/indexing and full Phase 2 exit checks are still pending. No active corpus, retrieval or answer service is created here.
