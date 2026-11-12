# Phase 2.1: HTML extraction

**Current downstream status:** The extraction-stage results below record this phase's scope and review state at completion. Shared normalization and [structure-aware chunking](phase2-chunking.md) are now implemented, and the Phase 2 ingestion development gate has passed. Native outputs still require exact downstream review receipts; uncertain OCR remains quarantined. Vector indexing and activation remain planned.

**Completed November 12, 2026: P2H.1, P2H.2 and P2H.3.** All ten currently selected HTML assets were extracted from their verified Phase 1 acquisitions: **178 ordered native blocks and two guidance tables**. The [review record](html-extraction-review.json) records source/version hashes, extraction identities, diagnostics and table comparisons. At this extraction checkpoint, PDF extraction, normalization, chunking and activation were still pending; the current downstream status above records subsequent completion. Extraction outputs retain `pending` review status.

## What this does, in simple terms

A downloaded webpage contains the article, menus, related links, pictures, scripts and other material. This step finds the reviewed article and turns its useful parts into small, ordered records. A record says what the text is, which section it belongs to, and where it came from.

For example, Minnesota's cold-storage page says freezer burn is a **food-quality issue, not a food safety issue**. The extractor keeps that warning together with its nested explanation. It also keeps the page's `Freeze` section and its `#free` anchor. Those details will help the later chatbot interpret and cite the warning.

Tables need extra care. The number `21` by itself means little. In the Minnesota chart, it belongs to **fresh eggs in the shell**, under **Refrigerator (Days at 40°F)**. The adjacent freezer cell says **don't freeze**. Extraction keeps the category, product, column labels, temperature, time unit and original cell positions together. It also keeps the chart's explanation that freezer times concern taste loss. A blank refrigerator cell for TV dinners stays blank.

Some pages mix useful guidance with sections outside the project's scope. The FDA nutrition-label page has label-reading instructions alongside calorie advice, medical claims and worked nutrient examples. Its profile retains the reviewed label explanations and excludes the other material. The useful statement that serving size is **not a recommendation of how much to eat** survives.

These records are the foundation for later processing. They have not yet been split into tokenizer-sized chunks, embedded or made available to the chatbot.

## How to run it

From the repository root, using the project's virtual environment:

```sh
python -m pip install --require-hashes -r requirements/html.lock
python -m dietary_rag extract-html mn_cold_storage \
  --report data/staging/html-extraction/single-report.json
python -m dietary_rag extract-html --all-eligible \
  --report data/staging/html-extraction/report.json
```

`--all-eligible` selects only the registry's eligible HTML sources. Alternatively pass explicit document IDs. `--output-dir` changes the export directory; `--config` and `--project-root` work like the existing acquisition commands.

The command reads the existing SQLite acquisition catalog and raw artifact store. For each source, it chooses the latest reviewed retrieval in the current registry snapshot, breaking identical-time ties by version ID. That choice is stated in the report. It verifies the actual bytes before extraction and writes one JSON file named by extraction ID. Running it again with unchanged inputs produces the same identity and file contents.

Exit code `0` means selected sources extracted successfully, `1` means a source failed, and `2` means invalid configuration/selection or an unavailable catalog. A failure report identifies the source and reason. A source with changed or corrupt bytes, a missing acquisition, or an unsupported layout cannot produce evidence. The acquisition catalog remains read-only.

## Technical foundations

The implementation lives in [the parser](../src/dietary_rag/ingestion/html.py), [profiles](../src/dietary_rag/ingestion/html_profiles.py), and [native contracts](../src/dietary_rag/ingestion/html_contracts.py).

1. **HTML becomes a DOM tree.** Beautiful Soup, using Python's `html.parser`, interprets tags as parent/child nodes. The extractor follows that tree in source order. It does not execute JavaScript, calculate CSS layout or fetch linked pages.
2. **A profile selects the article.** CSS selectors choose the Minnesota article field, the CDC center guidance column, or the FDA inset content column. Exactly one root is required. A selector matching zero or multiple roots fails explicitly; the parser does not fall back to collecting the entire page.
3. **Reviewed exclusions control retained content.** Profiles contain allowed sections, excluded subsections, specific excluded wording and, for the mixed FDA label page, a paragraph allowlist. Selection fingerprints detect registry exclusion changes and require a corresponding mask review. Rights, audience, role, dates and attribution must also match the reviewed source.
4. **Ordered typed blocks preserve context.** `ContentBlock` now supports headings. Paragraphs, nested recommendation lists, notes and tables retain sequence indexes and heading paths. HTML `SourceLocation` includes the actual source anchor where available and a DOM path identifying the original element. No anchor or publication date is invented. All locations start as `pending`.
5. **Tables retain both structure and interpretation.** `HTMLCell` stores source row/column, spans, header status, scope, text and DOM path. The grid maps every expanded position back to an origin cell. `HTMLTableRow` attaches logical column headers and categories to each data row. `TableContext` keeps titles, units, categories/row labels, nearby qualifications and referenced notes. Spanned headings such as `Storage` above refrigerator/freezer columns become explicit compound headers. Missing cells, invalid/overlapping spans, nested tables and missing headers fail instead of being flattened into unreliable prose.
6. **Identities explain reproducibility.** Block IDs include document-version identity, location, content, order and configuration. The configuration includes profile rules, the reviewed selection fingerprint, parser version, Beautiful Soup version and Python HTML-parser version. Extraction IDs include the full native result. Changes to those inputs produce new identities.

The shared `Extractor.extract()` interface returns blocks. `HTMLExtractor.extract_report()` additionally returns native table structures, the original document metadata and diagnostics. Pydantic validation checks table-grid references, row widths, block order and document/table relationships.

`original_html` is the table's parsed DOM serialization **before exclusion processing**; exact original bytes remain in the content-addressed artifact store. It is an administrative comparison artifact and can contain excluded wording. Later consumer processing must use retained blocks/rows, not this original comparison field or diagnostics.

The lightweight `html` dependency extra is separately hash-locked. PDF, OCR and model packages are not needed to use the HTML extractor API.

## Profiles and source comparisons

| Source | Blocks | Guidance tables | Reviewed handling |
| --- | ---: | ---: | --- |
| CDC prevention | 52 | 0 | Prevention/recalled-food sections; preserve washing and thermometer warnings. |
| Minnesota temperatures | 11 | 0 | Exclude color-based doneness wording; retain the nested 160°F thermometer recommendation and hot-weather one-hour condition. |
| Minnesota cold storage | 15 | 0 | Preserve nested freezer-quality warnings and original anchors; remove page navigation. |
| Minnesota storage chart | 8 | 1 | Interpret legacy ordinary-cell headers, product spans and category bands; keep 2007 revision and refrigerator/freezer distinctions. |
| Minnesota thermometers | 6 | 0 | Treat the one-row introductory presentation table as a note; retain placement and cleaning instructions. |
| FDA seafood | 31 | 0 | Preserve the exact 145°F sentence while removing its visual-doneness alternative; retain shellfish purchasing and freezing qualifications. Remove the reviewed duplicated serving introduction. |
| FDA produce | 16 | 0 | Retain preparation/storage sections and pre-washed-produce qualifications; exclude illness/treatment content. |
| CDC pregnancy food safety | 4 | 1 | Preserve nine food categories, riskier/safer columns, pasteurization conditions and three-minute rest instruction; remove mercury-category lists as whole excluded subtrees. |
| FDA nutrition labels | 20 | 0 | Retain serving-size semantics, %DV definitions, 5%/20% thresholds, comparisons and label variations; exclude calorie/goals/medical/worked-value sections. |
| CDC food emergencies | 15 | 0 | Preserve outage/flood disposal conditions and warnings; exclude bleach and commercial-can recovery procedures. |

All **128 original Minnesota guidance-table cells** and **45 CDC table cells** were compared with extracted cells, including span relationships and numeric tokens. Minnesota's chart produces 28 data rows; CDC's table produces nine category rows. The two CDC seafood cells change only by removal of the reviewed mercury lists; retained safety numbers and conditions match the source. The JSON review record carries the exact cell counts and changed-cell indexes.

CDC's action-card labels are handled as contextual notes rather than document subsection boundaries. This prevents an internal “Steps to take” heading from ending the excluded floodwater-procedure section. Likewise, preserving a nested recommendation is a specific Minnesota rule: other excluded list subtrees, including mercury categories, are removed together with their children.

## Verification and limits

The complete suite passes **162 tests** on Python 3.13, including 31 focused HTML/CLI checks. Ruff lint/format, hash-lock installation, dependency consistency and wheel build pass. The real acquisition-backed command succeeds for all ten HTML assets. Portable fixtures retain the source content roots and titles, with a [manifest](../tests/fixtures/html/manifest.json) recording origin and fixture hashes; they are explicitly source-derived DOM fixtures, not original acquisitions. Synthetic tests cover spans, empty values, footnotes, responsive duplicates, hidden/noisy content, changed layouts, malformed tables, exclusions, provenance corruption and repeated exports.

Explicitly marked responsive copies with identical text are deduplicated; ordinary repeated guidance stays unless a source-specific duplicate was reviewed. Discovery/reference sources are rejected before extraction. Unsupported dynamic/CSS layouts need a new reviewed profile; browser rendering and JavaScript execution are outside this adapter.

Automated comparisons verify extraction fidelity, not full semantic approval of every recommendation. Visual-only guidance is omitted and disclosed. Native outputs remain pending the later normalization, exclusion/quality review and activation gates. The broader Phase 2 exit gate still requires PDF extraction, OCR, normalization and chunking.
