# dietary-guidance-rag-chatbot

Build a prototype for a US audience using **HTML pages and PDF documents**, prioritizing authoritative government guidance. Every factual claim must come from retrieved evidence and carry a citation, including page references for PDFs. The service reports evidence gaps and declines medical advice and calorie/weight targets.

The [source registry](config/sources.yaml) records 68 inspected candidate URLs and retains all 20 earlier IDs. The [source review](docs/sourceReview.md) and [P1.7 coverage review](docs/phase1-coverage.md) document access, audience, currency, rights, alternate formats and 12 verified official PDFs; 9 PDFs are approved with exclusions. The default selection has **16 eligible consumer families: 10 HTML assets and 6 PDFs**, all imported and byte-verified under the current review snapshot. This meets the initial acquisition coverage target; extraction/indexing gates remain separate.

See the [problem statement](docs/problemStatement.md) and [architecture](docs/architecture.md) for HTML/PDF extraction, OCR fallback, cleaning, normalization, chunking, indexing and citation requirements. Phase 0 provides the Python package, settings, contracts and identity helpers. P1.1/P1.2 add registry eligibility, immutable raw-byte artifacts and SQLite provenance storage. **P1.3–P1.6 implement reviewed-file imports, actual-format inspection, bounded HTTPS fetching and acquisition commands/reports.** **Phase 2.1 implements HTML extraction for all ten selected HTML assets.** **Phase 2.2 implements native PDF extraction through P2P.4.** **Phase 2.3 implements OCR fallback through P2O.3.** **Phase 2.4 implements shared normalization and exclusions through P2N.3.** **Phase 2.5 implements structure-aware chunking through P2C.3.** Embeddings, retrieval and the chatbot remain planned. Downloaded source snapshots, acquisition outputs and inspection renders are ignored by Git.

Follow the [phase-wise implementation plan](docs/implementation-plan.md) for build dependencies, tasks, deliverables, learning outcomes and completion gates.

Use Python 3.11 or later; this machine has `python3.13`, while its default `python3` is 3.9. From the repository root:

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements/dev.lock
python -m pip install --require-hashes -r requirements/acquisition.lock
python -m pip install --require-hashes -r requirements/html.lock
python -m pip install --require-hashes -r requirements/pdf.lock
python -m pip install --require-hashes -r requirements/ocr.lock
python -m pip install --require-hashes -r requirements/chunking.lock
python -m pip install --no-deps --no-build-isolation -e .
dietary-rag check-settings
dietary-rag check-registry --include-decisions
python -m pytest
python -m ruff check src tests
python -m ruff format --check src tests
```

See [Phase 0 foundations](docs/phase0-foundations.md) for configuration precedence, synthetic contract examples, identity/version rules, dependency groups and wheel installation. Core, development, acquisition, HTML, native PDF, OCR and tokenizer Python dependencies are hash-locked; API and embedding-weight dependencies are reserved for later phases. OCR requires a separately installed Tesseract executable and language data. Acquisition does not require model/API credentials.

See [P1.1/P1.2 corpus foundations](docs/phase1-corpus-foundation.md) for consumer selection reasons, original-byte storage, SQLite usage and provenance checks. The registry check is read-only; repository initialization and writes require explicit API calls. It does not import the review cache or activate a corpus.

See [P1.3–P1.6 source acquisition](docs/phase1-acquisition.md) for a beginner-friendly explanation of each task, import recipes, outcomes, provenance and verification. To fetch two explicitly selected sources:

```sh
dietary-rag fetch-sources mn_cold_storage mn_cross_contamination_pdf \
  --report data/staging/acquisition-reports/live-two-source-fetch.json
```

Matching reviewed files are stored for extraction; changed files are retained as `needs_review` without replacing approved versions. This command prints a JSON report and returns exit code 1 if review is required. **Phase 1 is complete through P1.7, and HTML extraction is complete through P2H.3.** **Native PDF extraction is complete through P2P.4.**

Audit current eligibility and stored acquisitions:

```sh
dietary-rag audit-coverage
dietary-rag audit-coverage --verify-acquisitions \
  --report data/staging/acquisition-reports/coverage-audit.json
```

See the committed [coverage audit](docs/coverage-audit.json) for the verified milestone. The audit never downloads or changes approvals; storage verification reads the existing SQLite catalog and checks the actual files. It does not claim a published searchable corpus.

Validate the registry in the installed environment:

```sh
python scripts/validate_source_registry.py
# Optional later check: requires the local review cache and the ingestion dependency group.
python scripts/validate_source_registry.py --check-cache
```

See [Phase 2.1 HTML extraction](docs/phase2-html-extraction.md) for simple and technical explanations, profile decisions and source comparisons. Extract acquired HTML into native evidence and diagnostics:

```sh
python -m dietary_rag extract-html --all-eligible \
  --report data/staging/html-extraction/report.json
```

The verified run produces **178 ordered blocks and two contextual tables** from ten HTML assets. These records preserve headings, nested lists, source anchors/DOM paths, table units and qualifying notes. The command reads the acquisition catalog without changing it and writes inspectable JSON. Native outputs remain pending at this extraction stage; the implemented normalization and chunking stages below supply reviewed evidence. Retrieval and activation remain planned.

See [Phase 2.2 native PDF extraction](docs/phase2-pdf-extraction.md) for simple and technical explanations, page mappings, layout decisions and source comparisons:

```sh
python -m dietary_rag extract-pdf --all-eligible \
  --report data/staging/pdf-extraction/report.json \
  --render-dir data/staging/pdf-extraction/renders
```

The six selected PDFs produce **265 native blocks across 30 pages and one contextual table**. Three approved alternate PDFs extend comparison coverage to 37 pages and three tables without changing default-family counts. Word/block geometry, physical and printed pages, categories, units, dates and qualifications survive; uncertain tables are quarantined. The [review record](docs/pdf-extraction-review.json) compares all 146 original table cells. Native outputs remain inspectable; shared normalization and chunking now supply fidelity-reviewed, attributable chunks as described below.

See [Phase 2.3 OCR fallback](docs/phase2-ocr-extraction.md) for page routing, reviewed image regions, original-coordinate transforms and confidence gates. After installing Tesseract (for example, `brew install tesseract` on macOS), run the project-created mixed fixture:

```sh
python -m dietary_rag extract-ocr \
  --file tests/fixtures/ocr/project-mixed-scan.pdf \
  --document tests/fixtures/ocr/document.json \
  --plan tests/fixtures/ocr/plan.json \
  --report data/staging/ocr-extraction/report.json
```

This deliberately returns exit code **1**: four clear-text blocks pass automatic checks, while the blurred temperature/time candidate is quarantined and produces no retained block. Native/sparse/blank pages skip OCR. The [OCR review record](docs/ocr-extraction-review.json) records actual Tesseract output, image hashes and source comparisons. Shared normalization is implemented; uncertain OCR stays quarantined. Reviewed chunking is implemented below; uncertain OCR remains unavailable.

See [Phase 2.4 normalization](docs/phase2-normalization.md) for common blocks, conservative text repairs, source masks and exact review receipts:

```sh
python -m dietary_rag extract-pdf --all-eligible \
  --report data/staging/normalization-inputs/pdf-report.json
python -m dietary_rag normalize-extractions \
  --reports data/staging/html-extraction/report.json \
            data/staging/normalization-inputs/pdf-report.json \
  --report data/staging/normalization/report.json
```

The 16 selected assets yield **443 canonical blocks and three tables**. Three approved PDF comparisons extend the review to 497 blocks and 250 retained table values. The [review bundle](docs/normalization-review.json) records transformations, source comparisons and receipts that mark only exact matching output reviewed for transformation/exclusion fidelity. NHLBI dining-card boundaries were corrected against the original page; uncertain OCR cannot be promoted. Normalization itself adds no dependencies or index; the implemented chunking stage follows below, with active indexing reserved for Phase 3.


See [Phase 2.5 chunking foundations](docs/phase2-chunking.md) for simple and technical explanations and [source/token/evaluation review](docs/chunking-review.json). Once exact reviewed normalization exports exist:

```sh
dietary-rag prepare-tokenizer
dietary-rag chunk-sources \
  --normalization-report data/staging/normalization/reviewed-report.json \
  --report data/staging/chunks/350-report.json
```

The **350-token target / 50-token sentence overlap** produces **269 default chunks** across 16 sources, with a maximum full E5 input of **337 tokens** against the verified **512-token limit**. Complete cards, recommendations, conditions and table rows define boundaries; short evidence stays short. Tokens include `passage:`, context and special tokens. The tokenizer/model revision is pinned; preparation downloads tokenizer metadata only. Builds are offline, deterministic and traceable through exact block slices, native table rows and original PDF regions/geometry in a separate staging SQLite store plus JSON exports.

The 250-token alternative produces 274 chunks. Both achieve 16/16 complete-evidence hits at five results in the authored offline BM25 diagnostic. Keep 350/50; dense E5 and broader retrieval evaluation remain Phase 3. Comparison evidence has separate snapshots; quarantined OCR and exclusions remain enforced. No active index or chatbot is created. See the phase guide for comparison commands, inspection examples, test/cache requirements and trade-offs.
