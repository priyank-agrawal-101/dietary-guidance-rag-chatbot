# Implementation Plan: Dietary Guidance RAG Chatbot

## 1. Purpose and Starting Point

This plan turns [architecture.md](architecture.md) and [problemStatement.md](problemStatement.md) into an ordered implementation backlog. It follows the architecture's seven main phases, adds a setup phase, and divides extraction into explicit HTML, PDF, OCR, normalization and chunking milestones. Each phase is divided into smaller subphases and individually reviewable tasks with stable IDs, dependencies and completion checks. Phase gates define full completion; task dependencies allow an earlier working development slice.

The objective is a US-focused chatbot that retrieves approved dietary and household food-safety guidance, answers only from retrieved evidence, and cites every factual claim. Learning to extract, clean, normalize, chunk and index **both HTML and PDF** is part of the implementation, not an optional extension.

### Current repository baseline

| Item | Current status | Implementation consequence |
| --- | --- | --- |
| Problem statement and architecture | Documented | Use these as requirements and design references. |
| [Source registry](../config/sources.yaml) | 68 reviewed candidate URLs; all 20 earlier IDs retained | Extend the existing registry instead of replacing it or creating a second source list. |
| [Source review](sourceReview.md) | 12 verified official US PDFs; 9 approved with exclusions | Acquisition approval still requires block-level extraction and quality validation. |
| Default consumer acquisition selection | 16 eligible families, all byte-verified in the current snapshot; 10 HTML and 6 PDFs | Initial family/PDF acquisition target met; exclusion-aware extraction and release checks remain. |
| [Registry validator](../scripts/validate_source_registry.py) | Implemented | Reuse it and extend its checks alongside runtime schemas. |
| Local review snapshots and PDF inspection artifacts | Available in ignored staging directories on the review machine | Useful development inputs; not a portable, published corpus or a substitute for acquisition/import commands. |
| Phase 0 package, settings, provenance contracts and identity helpers | Implemented and verified | See [Phase 0 foundations](phase0-foundations.md) for setup and contracts. |
| Registry eligibility, original-byte artifact store and SQLite provenance | P1.1/P1.2 implemented and verified | See [corpus foundations](phase1-corpus-foundation.md). |
| Reviewed HTML/PDF import, file inspection, bounded HTTPS fetch, acquisition CLI/reports | P1.3–P1.6 implemented and verified | See [source acquisition](phase1-acquisition.md) for actual import/fetch results and beginner explanations. |
| Coverage review and read-only audit | P1.7 complete | See [coverage review](phase1-coverage.md) and [verified audit](coverage-audit.json). |
| HTML extraction | P2H.1–P2H.3 implemented and verified | See [HTML extraction foundations](phase2-html-extraction.md) and [source comparisons](html-extraction-review.json). |
| Native PDF extraction | P2P.1–P2P.4 implemented and verified | Six selected PDFs and three approved comparison layouts; 37 physical pages and 146 original table cells reviewed. See [PDF foundations](phase2-pdf-extraction.md). |
| OCR fallback | P2O.1–P2O.3 implemented and verified in isolation | Nine-page project-created fixture, bounded region OCR, confidence/provenance and numeric/unit quarantine. See [OCR foundations](phase2-ocr-extraction.md). |
| Shared normalization and exclusions | P2N.1–P2N.3 implemented and verified | 443 default canonical blocks, 54 comparison blocks, 250 retained table values compared and exact review receipts; [normalization foundations](phase2-normalization.md). |
| Structure-aware chunking and staging persistence | P2C.1–P2C.3 implemented and verified | 269 reviewed default chunks, pinned 512-token E5 limit and SQLite/JSON inspection; [chunking foundations](phase2-chunking.md). |
| BGE-small embeddings and paired candidate indexing | P3.1–P3.3 implemented and verified | 269 normalized 384-dimensional vectors, complete-input checks and canonical SQLite/Chroma compatibility validation; [indexing foundations](phase3-indexing.md). |
| Scoped retrieval and strategy selection | P3.4–P3.6 implemented and verified | Dense/BM25/hybrid search, resolution and audits; hybrid passes 16/16 regressions and 32/32 held-out hits@5; [retrieval review](phase3-retrieval.md). |
| Active corpus lifecycle | P3.7 implemented and verified | Atomic validated publication, request/evidence pinning, retained proof and rollback; [lifecycle review](phase3-lifecycle.md). |
| Policy service and response contracts | P4.1–P4.6 implemented and verified | Code gates, typed routing/contracts and pinned retrieval integration; [policy foundations](phase4-policy.md). |
| Grounded answering and citation validation | P5.1–P5.7 implemented and verified | Conservative extractive CLI, typed provider/verifier adapters, canonical citations and bounded repair; [answering foundations](phase5-answering.md). |
| HTTP API integration | P6.1–P6.3 implemented and verified | Shared CLI/API orchestration, Groq token/usage budgets, health/readiness, source listing and deadline/error handling; [API guide](phase6-api.md). |
| Browser UI | P6.4–P6.6 implemented and verified | Source selection, comparisons, all response states and safe rendering; [UI guide](phase6-ui.md). Phase 7 learning-project release is complete under the documented Human–AI assisted gate revision. |

Registry counts reflect the November 11, 2026 source review; Phase 2 completion reflects November 12, 2026. Recompute coverage from the registry and reviewed build reports when sources change; do not hardcode these counts in runtime logic.

### Rules carried through every phase

- Prioritize applicable US government consumer guidance and preserve source type, jurisdiction and audience limits.
- Keep unavailable, usage-blocked and rights-unresolved sources recorded but unindexed. A public URL is not automatic ingestion permission.
- Keep medical diets, scientific advisory reports, historical references, regulatory food-service documents and optional international material outside default consumer retrieval.
- Decline medical advice, calorie targets, weight targets and advice about what someone should weigh through application code, with a professional referral.
- Route individual-food nutrient-value requests to `requires_structured_data`; the underlying database capability belongs to Milestone 3.
- Preserve source dates, editions, conditions, units, section context and PDF locations. Never invent a publication year or a missing table value.
- Count unique consumer guidance families rather than URLs, PDF pages or alternate HTML/PDF versions.
- Keep ingestion administrative and separate from chat requests. The initial chat service does not fetch arbitrary URLs or accept document uploads.

## 2. Phase Sequence and Dependencies

| Phase | Main outcome | Depends on | Exit evidence |
| --- | --- | --- | --- |
| 0. Setup | Reproducible project structure, settings and component contracts | Existing documents | Clean environment can load configuration and run baseline checks. |
| 1. Corpus foundation | Registry-driven acquisition, provenance and source eligibility | Phase 0 contracts/settings | Real HTML/PDF files and metadata are stored; failures and exclusions remain explicit. |
| 2. Extraction and chunking | Reviewed content blocks and chunks from HTML, native PDFs and an OCR fixture | Eligible acquired inputs from Phase 1 | Source comparisons demonstrate preserved text, tables, conditions and locations. |
| 3. Embedding and retrieval | Versioned candidate corpus with inspectable full-corpus and named-document search | Reviewed native blocks/chunks from Phase 2 | Vectors resolve to canonical evidence and all mandatory filters work. |
| 4. Policy and response contracts | Code-enforced scope routing and typed response/error states | Phase 0 contracts and Phase 1 eligibility; integrate with Phase 3 | Prohibited requests cannot reach answering; scope and failures are handled accurately. |
| 5. Grounded answering | Evidence assessment, supported per-document claims and code-rendered citations | Phases 3 and 4 | Validated claims trace to retrieved text and source locations. |
| 6. Cross-document interface | Complete API orchestration and minimal chat UI | Phase 5 | User-facing answers preserve source contributions and show all response states. |
| 7. Evaluation and operation | Release evidence, reproducible deployment and refresh/rollback workflow | Phases 1–6 | Documented release gates pass and known limitations are disclosed. |

Use task dependencies to choose the next executable change. A whole-phase gate is required to mark that phase complete, but it does not prevent downstream work on a reviewed subset. Native HTML/PDF work can reach a development answer while OCR, additional layouts and source coverage continue; uncertain/scanned content remains excluded until its quality gates pass. Every generated answer, including the first CLI demo, requires the full relevant policy, evidence and claim-validation path.

### Development milestones

| Milestone | Required task outputs | Demonstration | What remains before release |
| --- | --- | --- | --- |
| A. Two sources to chunks | P0.1–P0.5; P1.1–P1.3; P2H.1; P2P.1; P2N.1–P2N.2; P2C.1 | One eligible HTML page and one native PDF produce inspectable, excluded-section-safe prose chunks. | Tables/token budget, OCR, broader acquisition, retrieval and answering. |
| B. Searchable mixed corpus | Milestone A plus P1.4–P1.6; P2H.2; P2P.2–P2P.3; P2N.3; P2C.2–P2C.3; P3.1–P3.6 | Full-corpus and named-document retrieval over reviewed evidence, with source/page inspection. | Snapshot lifecycle, policy/answering, OCR and wider source/layout coverage. |
| C. First cited CLI answer | Milestone B plus P3.7; P4.1–P4.6; P5.1–P5.7 | A permitted question produces validated claims and HTML/PDF citations; refusal and evidence-gap cases also work. | Full OCR/layout quota, UI, release evaluation and coverage. |
| D. Browser prototype | Milestone C plus P6.1–P6.6 | Source selection, comparisons and all response states work in the browser. | Full source/format completion, evaluated coverage and operational release evidence. |
| E. Complete milestone release | All required tasks and phase gates, including P1.7, P2O.1–P2O.3, remaining layout tasks and P7.1–P7.7 | At least 5 official PDFs contribute reviewed searchable chunks; unique-family coverage, OCR and release checks pass. | Only documented future capabilities remain outside scope. |

Milestones A–D are **development checkpoints**, not relaxation of the final requirements. Do not mark Phase 2 complete without OCR/retained-layout validation or Phase 3 complete without its five-PDF demonstration. A smaller working corpus must be labeled clearly.

### Working efficiently

- Keep each task to one coherent output and one reviewable change. Split a task if it introduces a second independent component; do not merge extraction, retrieval and generation into one large implementation batch.
- Implement the first supported parser/profile fully, then extend it to selected sources. Avoid designing parsers for every candidate registry URL or installing every model/tool before it is needed.
- Reuse reviewed local snapshots for deterministic development, then verify the real fetch/import path. Network availability must not block every parser/test run.
- Keep normalized blocks, chunks and API orchestration shared across formats and entry points. Separate adapters handle HTML, native PDF and OCR; the UI calls the same validated workflow as the CLI.
- After P1.1, coverage task P1.7 can progress independently of parser work. After P0.4, policy tasks P4.1–P4.4 can progress while retrieval is built. After P2P.1, OCR classification can progress alongside native layout extraction.
- Cache artifacts by raw hash plus parser/normalizer/chunker configuration and exclusion-mask version. Reuse unchanged approved extraction/embeddings where valid, while still producing a complete versioned snapshot. Metadata/rights changes must invalidate eligibility/citation decisions even when text is unchanged.
- Add a small evidence-based query set in P3.6 and expand it in P7.1. Run checks relevant to the changed component; repeat broad checks at integration/release gates or when failures justify them.
- Defer visual polish, reranking, FTS5, persistent memory and distributed infrastructure until measured needs justify them. Table fidelity, OCR learning, scope enforcement and citations remain required.

## 3. Phase 0: Project Setup and Shared Contracts

**Status: complete — November 11, 2026.** Tasks P0.1–P0.5 are implemented. The package, settings, shared contracts, extraction interface, identity helpers, core/development hash locks, examples and test scaffolding are documented in [phase0-foundations.md](phase0-foundations.md). Verification: 44 tests passed; Ruff lint/format checks passed; editable and wheel builds succeeded; the wheel CLI and registry baseline passed in a clean core-only Python 3.13 environment. Generation remains unconfigured and no model/OCR dependencies are required.

**Goal:** Establish a reproducible skeleton and agree on the data passed between components before writing parsers or model orchestration.

### 0.1 Runnable project

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P0.1` | Create the installable Python package and minimal test layout. | Existing design | `pyproject.toml` and package imports work in a fresh environment; imports have no network side effects. |
| `P0.2` | Lock core dependencies and document optional OCR/model dependency groups. | P0.1 | Reproducible core install; expensive OCR/model installs are introduced when their tasks need them. |

### 0.2 Configuration and shared data

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P0.3` | Add validated settings and an environment template. | P0.1 | `config/settings.yaml` and `.env.example`; invalid values fail explicitly and secrets remain uncommitted. |
| `P0.4` | Define acquisition, document-version and content-block contracts. | P0.1 | Typed models preserve dates, eligibility and source locations; invalid required fields are rejected. |
| `P0.5` | Establish stable IDs/version rules and run baseline registry checks. | P0.2, P0.4 | Documented hash/version rules and existing validator output; later stages add their own chunk/answer contracts. |

### Deliverables

- Installable package and test scaffolding.
- Validated settings and documented dependency setup.
- Shared models and component interfaces, with example contract payloads.
- Development README section distinguishing implemented commands from future commands.

### Exit gate

- A fresh environment can install the package, load settings and run the existing registry validator.
- Required fields reject invalid payloads; unknown source dates remain null or explicitly labeled rather than receiving defaults.
- Importing modules does not fetch sources, download models or start ingestion automatically.

**Learning outcome:** Understand how typed contracts keep acquisition, parsing, retrieval and answering independently inspectable.

## 4. Phase 1: Corpus Foundation and Source Acquisition

**Goal:** Turn the reviewed registry into reproducible source acquisition and document-level provenance.

### 1.1 Registry and provenance store

**Status: complete — P1.1 and P1.2.** The registry loader/consumer filters, content-addressed artifact API, SQLite provenance repositories and read-only `check-registry` command are implemented. Their original verification passed 87 tests, Ruff lint/format and wheel-build checks at the earlier 61-source/10-family baseline. See [phase1-corpus-foundation.md](phase1-corpus-foundation.md). P1.3–P1.7 and the Phase 1 acquisition exit gate are now complete as described below.

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P1.1` | Load the existing registry and enforce consumer eligibility/family rules. | P0.4, P0.5 | Restricted, unavailable and reference-only records are excluded; IDs and review decisions remain intact. |
| `P1.2` | Implement raw-artifact storage and SQLite document/acquisition repositories. | P0.3, P0.4 | Original bytes, version metadata and hashes can be written/read without losing source date labels. |

### 1.2 Import, fetch and response validation

**Status: complete — P1.3–P1.6.** Reviewed Minnesota HTML and native-PDF files were imported with their original URL/time/hash evidence and repeat-import identities verified. Actual file checks, bounded approved-host/public-IP HTTPS fetching, `import-source`/`fetch-sources` commands and JSON reports are implemented. Live fetching reused the matching PDF artifact and retained changed HTML bytes as `needs_review`, preserving the approved version. Verification: 125 tests, Ruff lint/format, dependency consistency, registry validation and wheel build passed. See [source acquisition](phase1-acquisition.md) for commands, outcomes, limits and beginner explanations. No extraction or active corpus is claimed.

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P1.3` | Import one reviewed HTML file and one PDF with their original acquisition evidence. | P1.1, P1.2 | Two usable development inputs with intact URL/time/hash; importing a copy does not invent a new retrieval date. |
| `P1.4` | Implement actual-format detection and explicit acquisition failures. | P1.2 | PDF signature/open checks and HTML challenge/shell detection; HTML disguised as PDF is rejected. |
| `P1.5` | Add bounded HTTP fetching and inspected redirects. | P1.1, P1.4 | Timeouts, size limits, transient retry bounds and approved-host/private-destination checks work. |
| `P1.6` | Expose administrative import/fetch commands and a compact acquisition report. | P1.3, P1.5 | Commands record success/failure/provenance and reuse unchanged reviewed artifacts; no chat-time fetching. |

### 1.3 Coverage expansion

**Status: complete — P1.7 and the Phase 1 acquisition exit gate.** Existing IDs were retained, failed CDC/FDA sources recovered, additional official consumer pages and direct FDA PDFs inspected, and alternate editions/conflicting recommendations reconciled through recorded exclusions and separate roles. The registry contains 68 URLs, 12 verified/9 approved official PDFs, and 16 eligible families. All 16 selected assets, including 6 official PDFs, have current-snapshot versions with exact stored bytes verified by `audit-coverage --verify-acquisitions`. The real WHO HTML-as-PDF case remains rejected. Verification: 131 tests, registry/PDF-cache validation, Ruff, dependency consistency and wheel build passed. See [phase1-coverage.md](phase1-coverage.md) for sources, rationale, plain-language explanation, remaining domain gaps and [coverage-audit.json](coverage-audit.json). This is acquisition completion, not extracted chunks or a released index.

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P1.7` | Audit coverage, verify additional sources and reconcile alternate editions. | P1.1; continues alongside later tasks | Updated registry/review with at least 5 verified official PDF acquisitions and an honest unique-family count; work toward 15 consumer families. |

### Initial working inputs

Use eligible registry entries rather than duplicating URLs in this plan. Begin with an accessible Minnesota HTML guidance page, an HTML table, a short native-text PDF, an illustrated HHS PDF and a table-bearing PDF. Reuse the reviewed original files where their acquisition evidence is intact.

The dated Minnesota chart is an extraction/comparison asset, not default current federal guidance. The scientific report and older NIH guides may be separately inspected as reference benchmarks where rights allow; they do not become consumer sources to fill coverage quotas.

### Deliverables

- Registry/eligibility loader, fetcher/importer and acquisition CLI operations.
- Raw-source store and SQLite document/acquisition schema.
- Acquisition report listing success, failure, detected format, rights/role restrictions and metadata gaps.
- Updated coverage audit and justified registry changes when new sources pass review.

### Exit gate

- Both real HTML and real PDF responses can be acquired or imported with reproducible metadata.
- At least 5 official US PDF acquisition assets have verified direct URLs, issuer/title, page count, text classification and layout notes.
- The WHO-style case of a supposed PDF returning HTML is rejected as a PDF, even with HTTP 200.
- Failed, restricted and reference-only candidates remain outside the consumer build selection.
- Family counts are computed from eligible sources and show the actual shortfall, if any. Missing coverage does not prevent a clearly labeled development build.

**Learning outcome:** Distinguish source discovery, successful acquisition, ingestion eligibility and published evidence.

## 5. Phase 2: Extraction, Cleaning, Normalization and Chunking

**Goal:** Produce reliable retrieval units from both formats while preserving the information needed to interpret and cite guidance.

### 2.1 HTML extraction

**Status: complete — P2H.1–P2H.3, November 12, 2026.** Reviewed profiles cover all ten selected HTML assets. The acquisition-backed command produces 178 ordered native blocks and two contextual guidance tables, with original table structures, source anchors/DOM locations and extraction diagnostics. Source-derived portable fixtures and synthetic cases verify lists, spans/header relationships, responsive duplicates, discovery rejection and medical/calorie/process exclusions. Verification: 162 tests, Ruff lint/format, hash-lock installation, dependency consistency and wheel build passed. See [HTML extraction foundations](phase2-html-extraction.md) and [source comparisons](html-extraction-review.json). Outputs remain pending downstream review; this does not complete the broader Phase 2 exit gate.

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P2H.1` | Implement one HTML main-content parser against a reviewed page. | P1.3, P0.4 | Ordered paragraphs/headings/lists exclude navigation and scripts; source anchor/context survives. |
| `P2H.2` | Add contextual HTML tables and footnotes. | P2H.1 | Row/column spans, categories, units and safety/quality qualifications match the source. |
| `P2H.3` | Add only the additional HTML profiles needed by the selected corpus. | P2H.2, P1.6 | A table and a prose fixture pass; responsive duplicates and discovery summaries are handled explicitly. |

**Tasks:** Implement source-specific main-content profiles; remove navigation, advertisements, scripts and unrelated cards; retain headings, paragraphs, recommendations, lists, tables and qualifying notes. Expand row/column spans into logical headers and retain category labels, units and footnotes. Apply reviewed section exclusions without removing legitimate safety warnings.

**Outputs:** Ordered native HTML blocks, retained original table structures, extraction diagnostics and representative fixtures.

**Validation:** Compare extracted guidance against the original HTML. Include a list, a table with header relationships, responsive duplicate content and an excluded medical/calorie section. Reject discovery summaries as if they were complete guidance documents.

### 2.2 Native PDF text and layout extraction

**Status: complete — P2P.1–P2P.4, November 12, 2026.** Reviewed native profiles cover all six selected PDFs (265 blocks, 30 pages and one table) plus three explicitly selected approved alternates for comparison (319 blocks, 37 pages and three tables total). Physical pages, visible printed labels and PDF navigation labels remain distinct; native word/block/cell geometry, reading order, dates, captions and qualifications survive. All 146 original table cells were independently compared, and all original pages rendered for layout review. Portable source-derived fixtures and synthetic continuations, page mismatches, detector misses and ambiguous cells verify quarantine behavior. Verification: 188 tests, Ruff lint/format, hash-lock installation, dependency consistency, registry validation and wheel build passed. See [PDF extraction foundations](phase2-pdf-extraction.md) and [source comparisons](pdf-extraction-review.json). Outputs remain pending downstream review; comparison exports do not alter default selection or activate a corpus.

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P2P.1` | Extract native text with physical pages and printed labels from one short PDF. | P1.3, P0.4 | Selectable content retains correct page identity and original file provenance. |
| `P2P.2` | Add layout-aware blocks and repeating header/footer handling. | P2P.1 | Two-column reading order, captions and meaningful notes survive rendered-source comparisons. |
| `P2P.3` | Extract canonical PDF tables and continuation relationships. | P2P.2 | Reviewed cells/units remain attached to headers; missed detections and ambiguous cells are flagged. |
| `P2P.4` | Review additional selected PDF layouts and page-mismatch fixtures. | P2P.3, P1.6 | Retained safety tables and multi-page context are checked; visual-only omissions are recorded. |

**Tasks:** Implement page-aware extraction using pypdf/pdfplumber. Preserve word/block coordinates and reading order, including two-column layouts. Record physical page indexes separately from printed labels. Detect repeating page furniture, captions, meaningful footers, tables and continuation headers. Record omitted visual-only information instead of inventing textual guidance.

**Outputs:** Native PDF blocks, page-location mappings, canonical table/cell representations and rendered review samples.

**Validation:** Check a short fact sheet, an illustrated multi-column document and a dense storage table. Compare all retained safety-critical table values and units with the original. Test a physical/printed page-number mismatch and a multi-page recommendation/table fixture. A detector returning zero tables must not silently flatten a visible table into unreliable text.

### 2.3 OCR fallback

**Status: complete — P2O.1–P2O.3, November 12, 2026 (isolated extraction/quality checks).** Native, sparse, blank, scanned and mixed pages are classified; only explicitly reviewed raster regions receive bounded OCR. A nine-page project-created fixture demonstrates five OCR regions, quarter-turn/skew corrections, original word geometry, engine/language/image provenance and duplicate-native-text prevention. Real Tesseract yields four matching pending blocks and one quarantined blurred numeric/unit candidate, with no retained block for the uncertain text. Verification: 224 tests including 36 OCR checks, Ruff lint/format, hash-lock installation, dependency consistency, registry validation and wheel build pass. See [OCR foundations](phase2-ocr-extraction.md) and [review record](ocr-extraction-review.json). The original OCR milestone used existing shared block/location contracts; P2N.1–P2N.3 now implement the shared normalization dependency below. Uncertain OCR, chunking and activation gates remain separate.

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P2O.1` | Classify native, sparse/blank and scanned content pages. | P2P.1 | Native and intentionally blank pages do not receive duplicate/needless OCR. |
| `P2O.2` | Add bounded rendering/OCR with original geometry and confidence. | P2O.1, P0.2 | A labeled scan fixture yields traceable OCR blocks; rotation/skew handling is bounded. |
| `P2O.3` | Test mixed pages and quarantine uncertain numeric/units content. | P2O.2, P2N.1 | Low-quality content cannot enter answering until reviewed; synthetic scans are labeled accurately. |

**Tasks:** Classify pages by native-text presence and quality. For image-based content pages, render at an appropriate resolution, correct rotation/skew when necessary and run bounded OCR. Preserve engine/version, language, confidence, bounding boxes and original page-image provenance. Avoid duplicating existing native text, and distinguish intentionally blank pages from scans.

**Outputs:** OCR adapter, labeled native/scanned/mixed-page fixtures and quality/quarantine records.

**Validation:** Demonstrate OCR on a small licensed or project-created scanned fixture. The reviewed official PDFs predominantly contain native text and do not alone prove OCR works. Include a low-confidence numeric/unit case and verify it remains unavailable for answering until reviewed. Using a rendered page from an eligible PDF can provide a synthetic scan fixture, clearly labeled as synthetic rather than a naturally scanned official publication.

### 2.4 Shared cleaning, normalization and exclusions

**Status: complete — P2N.1–P2N.3, November 12, 2026.** HTML, native PDF and OCR use common canonical blocks with original-location/native-artifact links, cautious Unicode/whitespace and reviewed line-wrap repairs, source-specific paragraph/region masks and exact quality receipts. All 16 selected assets produce 443 reviewed canonical blocks and three tables; three explicit comparison PDFs add 54 blocks/two tables without increasing consumer-family counts. All 250 retained table values and qualifiers match native outputs. NHLBI dining-card leakage found during this review was corrected in the native source geometry and independently checked against five original-page crops. HHS audience/nutrient context and safety negations survive; calculator/supplement/clinical/lookup/media exclusions remain enforced. Uncertain OCR remains quarantined. Verification: 265 tests including 41 normalization checks, Ruff lint/format, dependency consistency, registry validation, reproducible CLI review exports and wheel build pass. See [normalization foundations](phase2-normalization.md) and [exact source review/receipts](normalization-review.json). This P2N review covers transformation/exclusion fidelity. Subsequent P2C completion and the Phase 2 ingestion gate are recorded below; indexing and activation remain planned.

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P2N.1` | Normalize native HTML/PDF outputs into common blocks. | P2H.1, P2P.1 | Whitespace/Unicode are consistent; dates, units, negations and original locations remain intact. |
| `P2N.2` | Implement source-specific paragraph/region exclusion masks. | P2N.1, P1.1 | Prohibited target/treatment/lookup passages and unlicensed media are excluded before chunking. |
| `P2N.3` | Review transformation fidelity and adjacent-column leakage. | P2N.2, P2H.2, P2P.3 | Eligible prose/table values and qualifiers match originals; exclusions do not leak through nearby blocks. |

**Tasks:** Convert both format outputs to the common block schema. Normalize whitespace/Unicode, cautiously join line-wrap hyphenation and remove repeated furniture while preserving true hyphenated terms, negations, fractions, temperatures, ranges and footnote links. Keep original extraction artifacts so transformations can be inspected.

Implement paragraph/region-level exclusion masks for medical treatment, calorie/weight targets, supplement dosing, individual-food nutrient lookups and unlicensed media. Review adjacent columns and callouts so a retained paragraph does not accidentally carry excluded text.

**Outputs:** Canonical reviewed blocks, original-location links, transformation versions and exclusion reasons.

**Validation:** For the NHLBI sodium sheet, retain eligible general tips while excluding page-1 treatment callouts and the page-2 food/sodium lookup table. For HHS sheets, remove prohibited calculator/supplement passages and retain age/audience context. Compare transformed values and qualifiers against originals; never repair gaps with generated guesses.

### 2.5 Heading-aware and table-aware chunking

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P2C.1` | Define chunk contracts and implement coherent prose/list chunks. | P2N.2, P0.5 | Heading paths, conditions and deterministic IDs survive; a basic 1-HTML/1-PDF development slice is inspectable. |
| `P2C.2` | Add labeled table chunks and tokenizer-budget enforcement. | P2C.1, P2N.3 | Headers, footnotes and page spans stay attached; complete embedding inputs fit without truncation. |
| `P2C.3` | Persist reviewed blocks/chunks and emit inspection/quality reports. | P2C.2, P1.2 | Canonical evidence resolves to original regions; README records chosen chunking parameters/trade-offs. |

**Tasks:** Build chunks within coherent heading paths. Keep recommendations with conditions/explanations, split oversized prose at meaningful boundaries and retain numbered-list context. Serialize table rows with title, category, column labels, units and applicable notes. Repeat essential headers for split tables.

Use the accepted **350-token preferred target / 50-token complete-sentence prose overlap**. Short complete evidence stays short; preserve atomic recommendations and exceptions even above the preferred target when they fit. Count the entire embedding input, including `passage:`, compact original document/section/audience context, body, repeated table labels and special tokens. Use the hash-pinned `intfloat/e5-base-v2` tokenizer at revision `f52bf8ec8c7124536f0efb74aca902b2995e5bcd` and enforce its verified **512-token limit** without truncation; quarantine unfittable atomic evidence.

Headings provide context/metadata rather than standalone embeddings. Merge prose only within the same section and reviewed region. HHS food-group cards and NHLBI dining tips remain separate through native layout references. Split oversized lists between top-level items with nested explanations, numbering and introductions retained. Do not overlap across sections, independent cards or table rows. PDF page breaks alone do not split a recommendation; retain contributing pages and rectangles.

For tables, group Minnesota storage rows within the same category and budget, repeating temperature headers and the quality note while preserving blanks. Use one CDC pregnancy category row with both choices and pregnancy context. Keep the six-row Minnesota grilling table together when the labeled input fits, otherwise use row groups with its qualification repeated. Serialize deterministically and preserve original HTML/PDF attribution.

Consume fidelity-reviewed default evidence only; comparison snapshots stay separate and quarantined OCR remains unavailable. Compare 350 against 250 using cooking/storage, hot-weather, pregnancy and label questions before changing defaults; the current offline BM25 result is diagnostic, with dense BGE-small evaluation planned in Phase 3.

Attach document/version IDs, block IDs, section path, canonical text, hashes, source dates/edition, format, URL, audience/role/family eligibility, table conditions and PDF page/geometry/extraction provenance. Generate deterministic chunk IDs from version, location, content and chunker configuration.

**Outputs:** Chunker, SQLite block/chunk storage, inspection exports and README chunking explanation with examples and trade-offs.

**Validation:** Identical inputs/settings produce stable chunks; meaningful content changes produce new identities. Every chunk fits the real tokenizer limit and resolves to original blocks. Table rows cannot lose refrigerator/freezer distinctions or safety-versus-quality qualifiers.

**Status: complete — P2C.1–P2C.3, November 12, 2026.** The accepted structure-aware baseline uses a 350-token preferred target and up to 50 tokens of complete-sentence prose overlap. Exact block slices, nested/numbered lists and introductions, independent reviewed PDF cards, and labeled table rows retain qualifications and original locations. The pinned E5 revision verifies a 512-token full-input limit with truncation disabled. The 16 reviewed default sources produce 269 chunks (96 prose, 152 list, 21 table); maximum full input is 337 tokens. Three alternate sources produce 42 separate comparison chunks; quarantined OCR remains unavailable. A separate transactional SQLite store and deterministic JSON build/snapshot exports preserve canonical/native evidence links. The 250-token alternative produces 274 chunks; both achieve 16/16 complete-evidence hits@5 in a small authored offline BM25 diagnostic, so defaults stay 350/50 pending dense BGE-small evaluation in Phase 3. Verification: 336 tests with the real pinned tokenizer, reproducible default/comparison/rejection exports, Ruff, dependency checks, registry validation and wheel build. See [chunking foundations and commands](phase2-chunking.md), [review evidence](chunking-review.json) and [inspection examples](examples/chunking/inspection.json).

### Phase 2 exit gate

**Status: passed for the reviewed ingestion development corpus.** Both formats produce reviewed chunks; source-derived native and accurately labeled synthetic scanned/mixed fixtures exercise routing and quality gates. Ambiguous OCR remains quarantined, exclusions and comparison separation are verified, and table values/qualifiers retain native attribution. This gate does not activate a vector index or imply complete semantic/image understanding. Dense retrieval and release checks follow in later phases.

- Both HTML and PDF produce reviewed canonical blocks and chunks through the shared pipeline.
- Native, scanned and mixed-page handling is tested with accurately labeled fixtures.
- All retained safety-critical tables are reviewed; ambiguous cells/low-quality OCR remain quarantined.
- Excluded regions, repeated furniture and disallowed audiences do not leak into consumer chunks.
- HTML/PDF alternate versions remain attributable and do not inflate consumer source counts.
- Extraction review records state what was checked, omitted or unresolved; automated text extraction is not presented as complete semantic validation.

**Learning outcome:** Understand why text extraction, layout interpretation, cleaning, normalization and chunking are distinct steps.

## 6. Phase 3: Embeddings, Versioned Index and Retrieval

**Status: Phase 3 complete — P3.1–P3.7, November 12, 2026.** The selected **[BAAI/bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5)** model has MIT-licensed, open-source weights for local inference without API fees; local hardware and hosting costs still apply. Explicit hash-verified preparation, offline embeddings, separate historical chunking settings, paired candidate SQLite/Chroma snapshots and compatibility validation are implemented. The reviewed consumer candidate contains 269 vectors across 16 documents, including six official PDFs; BGE full-input lengths are 18 / 59 / 335 tokens with no truncation. See [indexing foundations](phase3-indexing.md), [embedding verification](embedding-index-review.json), [retrieval commands and measured selection](phase3-retrieval.md) and [retrieval verification](retrieval-review.json). Scoped dense/BM25/hybrid retrieval, name resolution and canonical search audits are implemented. Hybrid is the selected passing configuration; P3.7 implements validated atomic activation, request pinning and rollback. See the [snapshot lifecycle](phase3-lifecycle.md). Model comparisons remain optional follow-up work.

**Goal:** Retrieve inspectable, eligible evidence across the corpus or within named documents before adding generated answers.

### Model decision and chunk compatibility

| Setting | Selected value |
| --- | --- |
| Model | `BAAI/bge-small-en-v1.5` |
| Revision | `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` |
| License | MIT |
| Vector representation | 384 dimensions, CLS pooling, L2-normalized vectors |
| Distance metric | Cosine, configured consistently at build and query time |
| Complete input limit | 512 tokens, including special tokens and query instructions or passage context |

The [reviewed chunking baseline](chunking-review.json) contains **269 consumer chunks across 16 sources, including six approved official US PDFs**: 96 prose, 152 list and 21 table chunks. Retain the **350-token preferred target / 50-token overlap**. Full input lengths of 20 / 61 / 337 tokens (minimum / median / maximum) were measured with E5's tokenizer, not BGE's. Both the 350/50 and 250/50 builds achieved 16/16 complete-evidence hits@5 on the authored BM25 diagnostic; this does not establish dense retrieval or answer accuracy.

Preserve existing chunk IDs, canonical text, evidence links and historical E5 preprocessing/token-count records. Derive each BGE passage input by removing only the leading `passage: ` from the stored `embedding_text`, retaining the exact document, section, audience context and evidence body. Do not add an instruction to passages. Prefix each query exactly once with:

`Represent this sentence for searching relevant passages: `

The query prefix includes the trailing space after the colon. Record BGE-specific complete input hashes and token counts separately from the immutable Phase 2 chunk records. Verify every passage and query against the pinned BGE tokenizer and 512-token limit before inference, with truncation disabled. Reject over-limit inputs; do not silently shorten evidence or questions. If an existing atomic evidence unit cannot fit, report the incompatibility and require a separately reviewed rebuild.

Explicit model preparation downloads and hash-verifies the pinned weights, tokenizer and model/pooling metadata. Record dependency versions and preprocessing identity; subsequent embedding and search use verified local artifacts offline. Update runtime embedding settings to BGE-small as part of P3.1, while retaining the historical E5 tokenizer configuration needed to validate Phase 2 artifacts.

### 3.1 Embedding and candidate corpus

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P3.1` | Implement the pinned BGE-small adapter, explicit model preparation and runtime embedding settings. | P2C.1, P2C.2 | Verified local artifacts, passage/query preprocessing, tokenizer checks, CLS pooling, 384-dimensional normalized vectors and cosine metric are reproducible; no implicit downloads or truncation. |
| `P3.2` | Build SQLite evidence and persistent local Chroma vectors into one complete candidate snapshot. | P3.1, P2C.3 | Manifest records the exact reviewed chunk snapshot, model/tokenizer revisions, preprocessing, input hashes/counts, dimensions, metric and dependency versions; every vector resolves to canonical evidence and provenance. |
| `P3.3` | Add corpus/index compatibility validation. | P3.2 | Exact vector/chunk membership and provenance are verified; missing/extra vectors, model/preprocessing/dimension/metric mismatches and mixed versions fail before querying. |

### 3.2 Scoped search and inspection

**Retrieval strategy decision: compare before selecting a default.** Keep the pinned BGE-small model, existing chunk identities, 350/50 chunking and paired candidate snapshots. Compare BGE-small dense retrieval, deterministic BM25 lexical retrieval and dense-plus-BM25 hybrid retrieval through one scoped interface. Any of the three may become the default if it passes the same evidence, scope and provenance gates; dense retrieval alone is not required to pass activation gates when another strategy is selected.

An exploratory diagnostic over the 269 reviewed chunks produced:

| Strategy | Complete-evidence hits@5 |
| --- | --- |
| BGE-small dense | 15/16 |
| Existing BM25 diagnostic | 16/16 |
| Dense + BM25 rank fusion | 16/16 |

Dense retrieval missed the complete restaurant sodium-tips evidence. The hybrid experiment used equal-weight reciprocal rank fusion with constant 60 and the top 20 candidates from each branch. These are **exploratory results**, using exact cosine ranking over stored vectors and the existing authored questions, not scoped Chroma retrieval or frozen held-out evaluation. The lexical diagnostic used historical chunk embedding text; the planned BM25 retriever uses canonical context plus body without model-specific prefixes. Neither the preliminary scores nor the earlier chunking diagnostic establish a final winner.

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P3.4` | Implement comparable dense, BM25 and hybrid corpus-wide/selected-document retrieval behind a shared interface. | P3.3, P1.1 | Identical resolved document/version scope, US audience, consumer role, rights, source tier and family restrictions apply before ranking in every mode; candidate budgets and evidence-preserving deduplication are bounded. |
| `P3.5` | Resolve names/aliases and produce strategy-aware search inspection and audits. | P3.4 | Unknown, ambiguous and unavailable selections remain explicit; audits record searched documents, strategy/configuration, branch/fused ranks and canonical evidence without silent scope expansion. |
| `P3.6` | Compare all three strategies on regressions and frozen held-out questions, then select the best passing default. | P3.5 | A reviewed selection report records identical snapshots/scopes/budgets, complete-evidence hit@5, MRR@5, failures and performance; the selected versioned retrieval configuration passes all activation gates. |

### Retrieval strategy and budgets

**P3.4–P3.6 completion evidence:** actual scoped Chroma retrieval, prefix-free BM25 and RRF were compared over the same 269-chunk snapshot. All 48 quality questions were searched corpus-wide, with explicit document selections tested separately. BM25 achieved 16/16 regression and 31/32 held-out hits@5 (held-out MRR 0.878); dense achieved 15/16 and 32/32 (MRR 0.984); hybrid achieved 16/16 and 32/32 (MRR 0.984). Every strategy passed 104 scope checks with complete provenance resolution. Hybrid wins among passing strategies by held-out hit count; dense fails the regression gate. Frozen question/evidence receipts, failures, configuration identities and performance are recorded in the [retrieval review](retrieval-review.json). This is a small project-authored evidence diagnostic, with no claim of answer accuracy or independent external evaluation. P3.7 activation/rollback checks are implemented and verified separately in the [lifecycle review](phase3-lifecycle.md).

- Reuse the existing deterministic BM25 scoring approach over canonical document/section/audience context plus body, without E5 passage prefixes or BGE query instructions. Cache lexical statistics in memory for this small corpus. Do not add FTS5 or another persistent lexical index yet; all branches use the same eligible snapshot/scope.
- Start ordinary searches with **20 candidates per branch**. Dense ranks by cosine similarity; BM25 ranks by its lexical score. Hybrid uses equal-weight reciprocal rank fusion, summing `1 / (60 + rank)` across branches with ranks starting at 1, and returns at most 20 candidates. Never add raw cosine and BM25 scores together.
- For explicit comparisons and broad multi-document searches, preserve the planned **three-candidate contribution per document** and bounded overall budget, applying the same scope and contribution limits to all compared strategies. Audit documents searched and evidence omitted due to budgets separately. Deduplicate overlapping evidence without losing conditions/qualifications or combining different table rows.
- Version the retrieval strategy, lexical preprocessing/scoring, fusion settings and candidate/contribution budgets independently of the existing immutable embedding snapshot. Record the tested configuration in the selection report; a later configuration change requires compatible validation and evaluation before activation.

### Retrieval comparison, validation and selection

- Reuse the existing 16 questions in `tests/fixtures/chunking/queries.json` as regressions. Add 32 independently authored held-out questions: four each covering temperatures/rest periods, storage, handling exceptions, pregnancy, labels, produce, sodium and adult/child guidance. Include paraphrases, exact food names, numeric/unit distinctions, near-miss rows, conditions and original blanks. Freeze questions and reviewed expected evidence before evaluating held-out performance.
- Compare all three strategies on identical snapshots, resolved scopes, questions and candidate budgets. Existing case `document_ids` are acceptable evidence labels, **not automatic search restrictions**. Evaluate full-corpus retrieval separately from explicit document selections.
- Every eligible default must achieve **16/16 regression complete-evidence hits@5**, **at least 29/32 held-out hits**, **zero scope leakage** and **complete provenance resolution** before activation. A hit requires all expected facts and qualifications in one attributable chunk; a related but incomplete passage or incorrect table row is not a hit. Apply these gates to the selected strategy, not dense retrieval alone.
- Select among passing strategies by highest held-out complete-evidence hit@5, then highest **MRR@5 rounded to three decimals**. MRR@5 averages the reciprocal rank of the first complete-evidence hit within five results, using zero for misses. On equal quality, prefer **BM25, then dense, then hybrid** to minimize operational complexity. Tune only against regressions before evaluating the frozen held-out questions; record failures and block activation if no strategy passes.
- Keep results for every strategy, including dense, visible in the selection report. Report hit@5 and MRR@5 by prose/list/table type and guidance category, expected evidence, branch/fused ranks and near-miss failures. Measure warm end-to-end query latency, startup cost, memory and index size on the same machine; record query-embedding cost where applicable and the machine/dependency configuration used.
- Test table-row confusion, missing conditions/qualifications, original blank cells, excluded/comparison evidence, quarantined OCR, unknown/unavailable selections, official-only and named-document scope, model/configuration mismatches, corrupted snapshots and rollback. Activation requires zero scope leakage and complete vector-to-evidence provenance resolution.
- Defer model comparisons, cross-encoder reranking, query expansion, hierarchical retrieval and rechunking until measured failures justify additional experiments. Similarity scores measure relevance, not whether evidence supports an answer. Do not silently lower validation gates.

### 3.3 Snapshot lifecycle

**Status: complete — P3.7.** Activation rechecks candidate compatibility, frozen selection claims and the winning versioned configuration before atomically publishing one pair. Release records retain proof and frozen case files; requests pin the pointer once and resolve later evidence against the same repository. Rollback revalidates and restores the previous pair without moving in-flight requests. The reviewed hybrid pair is activated locally; isolated rollback and concurrent/failure cases are verified. See [lifecycle commands and checks](phase3-lifecycle.md) and [verification](lifecycle-review.json).

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P3.7` | Implement atomic activation of a compatible snapshot and passing versioned retrieval configuration, request pinning and rollback. | P3.3, P3.6 | Activation selects both versions together; in-flight evidence/citations use the pinned pair and the previous snapshot/configuration pair remains available for rollback. |

### Deliverables

- Pinned BGE-small adapter, explicit preparation workflow and reproducible corpus builder.
- Snapshot manifest, SQLite evidence, persistent local Chroma index and compatibility validation report.
- Shared dense/BM25/hybrid full-corpus/named-document retriever, resolver and strategy-aware search audit.
- Retrieval-only inspection workflow, 16 regression cases, 32 frozen held-out question/evidence cases, a three-strategy quality/performance comparison and reviewed selection report.
- Versioned retrieval configuration paired with the compatible corpus snapshot for activation, request pinning and rollback.

### Exit gate

- A development snapshot includes useful eligible chunks from both HTML and PDF, with at least 5 approved official US PDF assets contributing reviewed chunks to the consumer ingestion/indexing demonstration.
- Named-document queries never return another document's evidence; official-only requests exclude secondary sources.
- Audience/role/rights filters and duplicate-family defaults are enforced before evidence reaches answering.
- Every retrieved chunk, and every vector used by dense/hybrid retrieval, resolves to canonical text and complete provenance from the same snapshot. Snapshot compatibility checks remain required even if BM25 becomes the default.
- BGE-small uses the pinned revision, verified 512-token complete-input limit, CLS pooling, 384-dimensional normalized vectors and cosine distance; no inputs are truncated and historical Phase 2 evidence remains immutable.
- All three strategies are compared under identical snapshots/scopes/budgets. The selected strategy achieves 16/16 regression and at least 29/32 held-out complete-evidence hits@5, with selection by held-out hit@5, MRR@5 and the documented tie preference. Reports retain dense results, near-miss rows, ranking failures and latency/startup/memory/index-size measurements.
- Comparison-only chunks and quarantined OCR remain outside consumer indexes; scope tests show zero leakage and vector-to-evidence resolution is complete.
- Changing embedding configuration requires a compatible rebuild; activation cannot mix old vectors with new metadata.
- Failed validation or the absence of a passing strategy prevents activation; the tested versioned retrieval configuration is activated and pinned with its compatible snapshot, and rollback restores both versions together.

**Learning outcome:** Understand vector search, metadata filtering, candidate ranking and reproducible corpus versions.

## 7. Phase 4: Code-Enforced Policy and Response Contracts

**Status: complete — November 12, 2026.** P4.1–P4.6 implement deterministic prohibitions, a conservative offline typed classifier and injectable validated adapter, fixed templates, five conversational states and separate service errors. Policy precedes lazy pinned retrieval; invalid/unavailable scope cannot invoke ranking or fall back to the full corpus. The reusable post-generation policy hook withholds prohibited or uncertain claims. See [policy foundations](phase4-policy.md) for contracts, integration, review cases and limitations. Verification: 36/36 reviewed routing cases and 98 focused Phase 4 tests; full suite 529 passed, followed by 153 final policy/retrieval/settings/import checks. Ruff, dependency consistency and wheel build passed; [review report](policy-review.json). Generation/grounding remains Phase 5.

**Goal:** Control which questions may reach answering and distinguish conversational outcomes from service failures.

### 4.1 Scope decisions

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P4.1` | Define policy categories and deterministic refusal/capability rules. | P0.4 | Medical, calorie/weight and nutrient lookup cases route distinctly; ordinary guidance remains allowed. |
| `P4.2` | Add typed intent classification for paraphrases and ambiguity. | P4.1, P0.3 | Classifier uncertainty/failure cannot fall through to generation or override a deterministic prohibition. |
| `P4.3` | Implement fixed refusal, capability and clarification templates. | P4.1 | Professional referral and Milestone 3 routing contain no fabricated evidence or search history. |

### 4.2 Request contracts and runtime gates

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P4.4` | Define request, response-state and error schemas. | P4.1, P0.4 | Scope IDs/limits validate; five conversational states remain distinct from service errors. |
| `P4.5` | Integrate policy before retrieval/generation and document-scope validation. | P4.2, P4.3, P4.4, P3.5 | Prohibited or invalid requests cannot invoke answering; unavailable IDs do not trigger fallback search. |
| `P4.6` | Review policy cases and define the post-generation policy check. | P4.5 | Paraphrases, mixed intent and ordinary allowed cases pass; policy restrictions are reusable by claim validation. |

### Deliverables

- Policy service, typed classifier adapter and fixed response templates.
- API request/response/error schemas and document-scope validation.
- Reviewed policy cases covering ordinary allowed guidance, explicit prohibitions, paraphrases, ambiguity and mixed requests.

### Exit gate

- Prohibited requests cannot invoke unrestricted answer generation, even when related passages exist.
- Pre-retrieval refusals have no fabricated citations or searched-document lists.
- Individual-food nutrient requests receive the future-capability response rather than estimated values.
- Unknown/unavailable sources and dependency failures are not mislabeled as evidence gaps.
- General food-storage and food-selection questions still reach retrieval when allowed.

**Learning outcome:** Understand the difference between scope restrictions, evidence insufficiency, clarification and operational failure.

## 8. Phase 5: Grounded Answering and Citation Validation

**Status: complete — November 12, 2026.** P5.1–P5.7 implement condition-aware evidence decisions, structured per-document drafts, typed generator/verifier boundaries, canonical scope/snapshot/quote validation, numeric/qualifier and post-generation policy checks, code-authored HTML/PDF citations, one bounded repair and the active-corpus `answer-question` CLI. The default is an offline extractive generator and independent extractive entailment verifier; free-form provider-backed paraphrases require an explicitly injected semantic verifier. [Answering foundations](phase5-answering.md) documents the implementation and conservative coverage limits. The [active-corpus review](answering-review.json) passes 31/31 cases across all six active PDFs, source comparisons, gaps, refusals, validation/repair and dependency failures. Verification: 576 full-suite tests, 43 focused Phase 5 tests and 152 final integration/contract checks passed; Ruff, dependency consistency, wheel build and wheel CLI smoke passed.

**Goal:** Convert retrieved passages into understandable, supported claims with trustworthy source references.

### 5.1 Evidence and claim drafts

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P5.1` | Implement evidence sufficiency and essential-condition checks. | P3.6, P4.4 | Supported/partial/missing-detail/not-covered decisions match actual retrieved conditions and search scope. |
| `P5.2` | Implement a thin generator adapter and structured per-document claim contract. | P0.3, P4.4 | Model output supplies chunk IDs/support quotes, not source metadata; malformed drafts are rejected. |
| `P5.3` | Generate drafts from retrieved evidence behind the policy gate. | P5.1, P5.2, P4.6 | Only permitted evidence reaches generation; source instructions are treated as quoted data. |

### 5.2 Validation and citations

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P5.4` | Validate scope, snapshot IDs, source separation and exact quotes. | P5.3 | Unknown/stale IDs, mismatched quotes and cross-document claims are rejected. |
| `P5.5` | Add numeric/condition checks, semantic verification and post-generation policy. | P5.4, P4.6 | Unsupported values, omitted qualifiers and prohibited advice are withheld; verifier behavior is reviewed. |
| `P5.6` | Render citations from stored metadata and original locations. | P5.4, P2C.3 | HTML section links and direct PDF physical-page citations are valid; date labels remain honest. |
| `P5.7` | Wire bounded repair, partial answers and evidence-gap/service-failure handling. | P5.1, P5.5, P5.6, P3.7 | A cited CLI development answer works; one repair maximum, accurate gaps and source-separated claims are demonstrated. |

### Deliverables

- Evidence assessor, structured generator adapter and claim validators.
- Code-driven HTML/PDF citation renderer.
- Answer inspection artifacts linking each claim to retrieved chunks, quotes and original locations.
- Supported, partial, missing-evidence and validation-failure examples.

### Exit gate

- Every displayed factual claim has valid retrieved evidence and a complete adjacent citation.
- PDF physical page references resolve correctly, including multi-page chunks and printed-page mismatches.
- Invalid IDs, fabricated/mismatched quotes, unsupported values and omitted qualifiers are rejected.
- Missing-evidence messages identify the actual searched scope without supplying unsupported model knowledge.
- Medical/calorie/weight and individual-food lookup claims are withheld by code.
- Cross-document claims remain separate, and source disagreements are attributed without averaging values.

**Learning outcome:** Understand why relevant retrieval and syntactically valid citations do not by themselves prove an answer is grounded.

## 9. Phase 6: Cross-Document API and Chat Interface

**Goal:** Expose the validated workflow through a minimal, understandable interface.

### 6.1 API integration

**Status: complete — P6.1–P6.3, November 12, 2026.** FastAPI exposes liveness/readiness, pinned source availability and shared validated chat orchestration. Groq `openai/gpt-oss-120b` uses compact evidence selections, local verification and persistent rolling minute/hour/day request/token limits. Absolute deadlines and request IDs span every stage; provider details stay private. Verification passed 628 full-suite tests and 44 final Groq/API checks, plus Ruff, dependency and build checks; the [real active-corpus API review](api-review.json) confirms 16 available documents and two cited answers with mocked Groq transport. See [Phase 6 API integration](phase6-api.md) for configuration, endpoints, limits and deterministic verification. Authenticated [Groq smoke verification](groq-live-review.json) passes six HTTP cases: two cited answers use two live calls totaling 876 tokens; four pre-generation states spend nothing. P6.4–P6.6 and the Phase 6 development exit gate are now complete as described below.

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P6.1` | Add process health, corpus readiness and document-list endpoints. | P3.7, P4.4 | Liveness/readiness differ; active snapshot, source availability and metadata are visible. |
| `P6.2` | Add the chat endpoint around the validated CLI orchestration. | P5.7, P6.1 | API and CLI return the same validated outcomes; no duplicated policy/retrieval logic. |
| `P6.3` | Add request deadlines, stable request IDs and typed dependency failures. | P6.2 | Timeouts/model outages are service errors and provider details/secrets are not exposed. |

### 6.2 Minimal chat UI and comparisons

**Status: complete — P6.4–P6.6 and the Phase 6 development exit gate, November 12, 2026.** The same-origin `web/` client serves source selection, cited source-separated comparisons, partial gaps, refusal/clarification/capability states, dependency failures and rolling usage visibility. Text-only DOM construction, reviewed-URL checks and CSP prevent active scraped/generated markup. A corpus-access lock fixes overlapping embedded Chroma lifecycles discovered during UI verification. Verification: 634 full-suite Python tests, 42 UI tests, eight [real active-corpus UI flows](ui-review.json), native Chrome desktop/390-pixel inspection and installed-wheel asset serving. See [chat UI foundations](phase6-ui.md). Phase 7 release/semantic evaluation remains separate.

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P6.4` | Build the basic question form, document selection and API response rendering. | P6.2 | A named/full-corpus query works in `web/`; only completed validated answers are displayed. |
| `P6.5` | Render per-document comparisons and all gap/refusal/error states. | P6.4, P6.3 | Two-source results remain separated, with unsupported aspects attributed accurately. |
| `P6.6` | Add safe text/link rendering and inspect the integrated UI flows. | P6.5 | Scraped/generated text cannot inject HTML; approved citations and health/source failure states render correctly. |

### Deliverables

- API handlers, integrated runtime orchestrator and minimal web client.
- Document listing with current snapshot/availability information.
- UI demonstrations for all response categories and explicit comparisons.

### Exit gate

- A permitted question returns the same supported evidence/citations through the API and UI as through the inspection workflow.
- A two-document comparison remains grouped by document; one document's evidence gap is shown without invented claims or a broader fallback search.
- Selection errors, model/corpus outages and timeouts are visible as service failures.
- Malicious source text is displayed safely and cannot change execution behavior.
- Health readiness reports an absent/incompatible corpus explicitly, while process liveness remains separate.

**Learning outcome:** Understand how backend policy, evidence and citation decisions become user-visible behavior.

## 10. Phase 7: Evaluation, Release and Operations

**Goal:** Demonstrate the milestone on reviewed cases and make the build, deployment and refresh process reproducible.

### 7.1 Evaluation and coverage

**Status: P7.1–P7.3 complete for the learning project, November 12, 2026.** The frozen 71-case suite, active-pin diagnostics, indexed eligibility audit and human-review worksheet are documented in [Phase 7 evaluation](phase7-evaluation.md). Automated scoring passes 71/71 frozen cases after fixing all 25 answerability failures; the original 46/71 baseline is retained. The user explicitly authorized a [Human–AI assisted learning-only gate](review-gate-revision.md): 71/71 grounded, 71/71 condition-complete (100%), 71/71 policy-safe and zero critical errors. The three prior completeness findings are resolved; [fix verification](completeness-fix-review.json) records the changes and retained review notes. Independent human review is not claimed. The active index has 16 eligible unique families and six approved official US PDFs. All learning-scope release checks pass.

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P7.1` | Expand the existing small reviewed query set to approximately 60–80 cases. | P3.6, P5.7 | Expected categories, evidence, conditions and forbidden claims cover both formats and all required behaviors. |
| `P7.2` | Run structural checks and human-reviewed grounding/policy evaluation. | P7.1, P6.6, P2O.3 | Format/layout-specific results and error analysis; semantic acceptance targets documented before release scoring. |
| `P7.3` | Close the coverage gap and audit final eligibility/exclusions. | P1.7, P7.2 | At least 15 eligible unique consumer families and 5 official PDFs contributing reviewed chunks, or an explicit requirements revision; no quota inflation. |

### 7.2 Refresh and reproducible release

**Status: P7.4–P7.7 complete for the learning project, November 12, 2026.** Conditional administrative refresh preserves the active pointer, stages changed bytes and persists metadata review flags. Aggregate stage/state metrics and a single-process foreground deployment are implemented. The [operations runbook](operations-runbook.md) covers installation, build/OCR, evaluation, backups, refresh, promotion and rollback. [Refresh verification](refresh-review.json) uses actual approved original bytes with deterministic HTTP responses. The clean-environment runner and fail-closed, checksummed local bundle publisher retain [reproduction](reproduction-review.json) and [release-gate](release-review.json) reports. All 71 automated evaluation cases pass and 672 tests plus offline active-corpus API checks pass in isolated hash-locked wheel reproduction. The learning-only report/snapshot bundle is published at `data/releases/learning-v0.1.1`; [publication proof](learning-release-publication.json) verifies checksums and copied-index integrity. Existing development activation is retained. No live user deployment or independent human review is claimed.

| ID | Task | Depends on | Output and completion check |
| --- | --- | --- | --- |
| `P7.4` | Add conditional refresh and reviewed content/metadata change handling. | P1.6, P3.7 | A failed refresh preserves the valid snapshot; significant changes require re-review and rebuild. |
| `P7.5` | Add stage metrics and a reproducible single-process deployment. | P6.3, P7.4 | Persistent stores/configuration work; useful timings/state counts without routine raw health-question logging. |
| `P7.6` | Complete build, OCR, evaluation, refresh and rollback documentation. | P2C.3, P7.2, P7.5 | README/runbook accurately reproduce implemented commands and describe provider flow and limitations. |
| `P7.7` | Run clean-environment reproduction and publish the release report/snapshot. | P7.2, P7.3, P7.4, P7.5, P7.6 | Release gates pass, unresolved findings are disclosed and promotion/rollback evidence is retained. |

### Deliverables

- Curated evaluation dataset and versioned results/error analysis.
- Corpus release report, coverage/rights audit and documented limitations.
- Reproducible build/deployment procedure and operations runbook.
- Validated release snapshot and promotion/rollback evidence.

### Exit gate

- Both formats contribute reviewed searchable guidance, with at least 5 official US PDFs demonstrated through extraction, chunking, indexing and page citations.
- OCR fallback works on a labeled scan/mixed fixture; uncertain extraction remains quarantined.
- At least 15 unique consumer guidance families meet eligibility and review gates, or an explicitly documented revised requirement applies. Reference assets and alternate formats do not inflate counts.
- Required provenance, filters, policy boundaries, claim validation and per-document citation behavior pass their reviewed checks.
- Evaluation findings and remaining evidence gaps are visible; timeouts and outages remain separate from missing evidence.
- A clean environment can reproduce the build and run the service, and an invalid refresh cannot silently corrupt the active snapshot.

**Learning outcome:** Understand how evaluation, corpus freshness, failure handling and reproducibility support a usable prototype.

## 11. Requirement-to-Phase Map

| Requirement | Primary implementation phase | Verification phase |
| --- | --- | --- |
| Preserve registry and existing source IDs | 1 | 1, 7 |
| Inspect access, authority, rights, currency and audience | 1 | 1, 7 |
| At least 5 verified official US PDFs; mixed-format corpus | 1–3 | 3, 7 |
| HTML/PDF extraction and table fidelity | 2.1–2.2 | 2, 7 |
| Page-specific OCR with quality review | 2.3 | 2, 7 |
| Cleaning, normalization and scope exclusions | 2.4 | 2, 5, 7 |
| Heading/table-aware chunks with complete provenance | 2.5 | 2, 3, 7 |
| Full-corpus, named-document and official-only retrieval | 3 | 3, 6, 7 |
| Keep alternate families and specialized references separate | 1–3 | 3, 5, 7 |
| Code-enforced medical/calorie/weight refusals | 4–5 | 4, 7 |
| Separate nutrient lookup capability response | 4 | 4, 6, 7 |
| Evidence sufficiency and actual search-scope reporting | 3, 5 | 5, 7 |
| Supported per-claim citations, including PDF pages | 5 | 5–7 |
| Per-document comparisons without merged recommendations | 3, 5–6 | 6–7 |
| Immutable snapshots, compatible embeddings and refresh/rollback | 3, 7 | 3, 7 |
| README chunking trade-offs and reproducible operation | 2, 7 | 7 |

## 12. Progress Tracking and Scope Control

Use the stable task IDs above when creating issues or implementation checklists. Track each as `not_started`, `in_progress`, `blocked` or `done`, with its output, validation evidence and any remaining limitation. A task is blocked only by its actual dependencies or missing input; independent ready tasks may continue. Update completion status only when exit evidence exists, not when scaffolding or a happy-path demo is present.

| Phase | Status at plan creation | Evidence needed to mark complete |
| --- | --- | --- |
| 0 | Complete: P0.1–P0.5 | 44 passing tests, lint/format checks, settings CLI, locked installs, wheel smoke check and registry baseline; see Phase 0 completion record. |
| 1 | Complete: P1.1–P1.7 | Registry/provenance, file inspection, bounded fetch, commands and coverage audit verified. Current-snapshot stored bytes match all 16 selected assets, including 6 official PDFs. Acquisition gate passes; extraction/release checks remain Phase 2+. |
| 2 | Complete: P2H.1–P2H.3, P2P.1–P2P.4, P2O.1–P2O.3, P2N.1–P2N.3 and P2C.1–P2C.3 | Reviewed default HTML/PDF evidence yields 269 attributable staging chunks with pinned token limits; OCR quarantine and comparison separation pass the ingestion development gate. See the chunking review. |
| 3 | Complete — P3.1–P3.7 | Hybrid selected on frozen questions; validated atomic publication, pinned requests and rollback verified over the reviewed mixed corpus. |
| 4 | Complete — P4.1–P4.6 | Reviewed routing, fail-closed classification, typed contracts, actual scope isolation and pinned evidence verified; see [policy foundations](phase4-policy.md). |
| 5 | Complete — P5.1–P5.7 | Cited active-corpus CLI, 31/31 reviewed cases, per-document gaps and bounded validation/repair verified; see [answering foundations](phase5-answering.md). |
| 6 | Complete — P6.1–P6.6 | Shared Groq API, source selection, validated comparisons, all response states and safe desktop/mobile UI; [UI review](ui-review.json). |
| 7 | Complete for learning scope: P7.1–P7.7 | 71/71 automated cases; user-authorized Human–AI assisted review meets original thresholds with all three prior completeness findings resolved. 672 tests, clean reproduction, local report/snapshot publication and checksum/index validation pass. Independent human review is not claimed. See [operations](operations-runbook.md). |

With Phases 0–6 complete, Milestone D's browser prototype works over the gated, pinned corpus of 269 reviewed chunks from 16 documents, including six PDFs. API, CLI and browser share `AnswerService`; Groq selects compact evidence IDs while local validation preserves complete claims and citations. The expanded evaluation, refresh, metrics, deployment/runbook and release-gate tooling are implemented. The 25 answerability findings are fixed. P7.2/P7.7 and Milestone E are complete for the learning project under the explicitly revised reviewer requirement; independent human acceptance for actual users remains outside this learning release. Retain BGE-small, the passing hybrid configuration and 350/50 chunking. Broader population and live-provider evaluation would be required for an actual-user release. Reuse the source review, coverage audit, registry validator and acquisition pipeline, and recheck coverage when new extraction exclusions remove material.

Keep the following outside the initial build: individual-food nutrient database integration, personalized diet/weight plans, persistent chat memory, arbitrary user document ingestion, live web search at answer time, distributed ingestion jobs and multiple API workers. Optional FTS5 search or reranking should be added only when measured retrieval failures justify them. A separate scientific/medical reference experiment must not expand the consumer chatbot's permitted advice scope.
