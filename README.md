# dietary-guidance-rag-chatbot

Build a prototype for a US audience using **HTML pages and PDF documents**, prioritizing authoritative government guidance. Every factual claim must come from retrieved evidence and carry a citation, including page references for PDFs. The service reports evidence gaps and declines medical advice and calorie/weight targets.

The [source registry](config/sources.yaml) records 68 inspected candidate URLs and retains all 20 earlier IDs. The [source review](docs/sourceReview.md) and [P1.7 coverage review](docs/phase1-coverage.md) document access, audience, currency, rights, alternate formats and 12 verified official PDFs; 9 PDFs are approved with exclusions. The default selection has **16 eligible consumer families: 10 HTML assets and 6 PDFs**, all imported and byte-verified under the current review snapshot. This meets the initial acquisition coverage target; extraction/indexing gates remain separate.

See the [problem statement](docs/problemStatement.md) and [architecture](docs/architecture.md) for HTML/PDF extraction, OCR fallback, cleaning, normalization, chunking, indexing and citation requirements. Phase 0 provides the Python package, settings, contracts and identity helpers. P1.1/P1.2 add registry eligibility, immutable raw-byte artifacts and SQLite provenance storage. **P1.3–P1.6 implement reviewed-file imports, actual-format inspection, bounded HTTPS fetching and acquisition commands/reports.** **Phase 2.1 implements HTML extraction for all ten selected HTML assets.** **Phase 2.2 implements native PDF extraction through P2P.4.** **Phase 2.3 implements OCR fallback through P2O.3.** **Phase 2.4 implements shared normalization and exclusions through P2N.3.** **Phase 2.5 implements structure-aware chunking through P2C.3.** **P3.1–P3.6 implement BGE-small candidate indexing, scoped dense/BM25/hybrid retrieval and frozen strategy selection.** **P3.7 implements atomic activation, request pinning and rollback.** Phases 4–5 implement policy and grounded answering; P6.1–P6.3 implement the Groq-backed API. P6.4–P6.6 implement the chat UI and source comparisons. Downloaded source snapshots, acquisition outputs and inspection renders are ignored by Git.

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

See [Phase 0 foundations](docs/phase0-foundations.md) for configuration precedence, synthetic contract examples, identity/version rules, dependency groups and wheel installation. Core, development, acquisition, HTML, native PDF, OCR and tokenizer dependencies are hash-locked; embeddings/indexing now have a separate macOS ARM64/Python 3.13 wheel lock. API dependencies are pinned in `requirements/api.lock`. OCR requires a separately installed Tesseract executable and language data. Acquisition does not require model/API credentials.

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

The 250-token alternative produces 274 chunks. Both achieve 16/16 complete-evidence hits at five results in the authored offline BM25 diagnostic. Keep 350/50; the frozen P3.6 retrieval comparison selected hybrid. Comparison evidence has separate snapshots; quarantined OCR and exclusions remain enforced. No active index or chatbot is created. See the phase guide for comparison commands, inspection examples, test/cache requirements and trade-offs.

## BGE-small candidate indexing (P3.1–P3.3)

Pinned BGE-small embeddings, explicit model preparation and paired candidate
SQLite/Chroma snapshots are implemented. The reviewed candidate has **269
normalized 384-dimensional vectors across 16 documents, including six PDFs**.
Complete BGE inputs measure **18 / 59 / 335 tokens**, within the verified
512-token limit without truncation. Canonical evidence and historical E5 chunk
records remain unchanged.

```sh
python -m pip install --require-hashes -r requirements/embeddings.lock
dietary-rag prepare-embeddings
dietary-rag build-index \
  --chunk-snapshot chunk_snapshot_e9e8b804e406477b528835fd71163f40bcffa5bf85c3552168300ecef26fe921 \
  --report data/staging/index-build-report.json
dietary-rag validate-index --snapshot-dir <snapshot_dir from the build report>
```

The dependency lock is specific to macOS ARM64/Python 3.13; other environments
need separate resolution and verification. Model preparation explicitly downloads
hash-pinned MIT-licensed weights. Subsequent embedding and validation work offline.
Compatibility validation checks exact membership, reviewed provenance, model
configuration, full input limits, canonical vectors and scalar metadata.
Candidates stay inactive; scoped search and strategy selection are implemented
through P3.7, including validated activation and rollback. See [indexing foundations](docs/phase3-indexing.md) and
[verification evidence](docs/embedding-index-review.json).

## Scoped retrieval and selection (P3.4–P3.6)

Search the reviewed candidate with `dense`, `bm25` or `hybrid`. Every mode
resolves the same scope before ranking and returns canonical evidence, source
locations, actual searched documents and branch/fused ranks.

```sh
dietary-rag search-corpus "What requests reduce sodium at a restaurant?" --strategy hybrid
dietary-rag search-corpus "How long can hard cooked eggs keep?" \
  --strategy bm25 --documents mn_storage_chart
dietary-rag search-corpus "According to Nutrition Facts label, what is a low Daily Value?" \
  --strategy hybrid
dietary-rag evaluate-retrieval --regressions-only
dietary-rag evaluate-retrieval --output-dir data/staging/retrieval/p3-review
```

The frozen comparison selected **hybrid**: 16/16 regression and 32/32 held-out
complete-evidence hits@5. BM25 scored 16/16 and 31/32; dense scored 15/16 and
32/32. All three passed scope/provenance checks. The selected pair is activated locally through the separate lifecycle pointer. See [retrieval guide](docs/phase3-retrieval.md)
and [review report](docs/retrieval-review.json) for frozen evidence, failures,
performance, versioned configuration use and test coverage. P3.7 activation and rollback are implemented; policy and grounded answering are implemented below.

## Active corpus lifecycle (P3.7)

See [terminal testing and output explanations](docs/terminal-testing.md) for
step-by-step commands, stored embedding inspection and how we verified search.

The validated hybrid pair is activated locally. Current state is recorded in
`data/active/active.json`; immutable candidate build reports retain their original
status. Requests pin both versions, and previous releases remain available for
validated rollback.

```sh
dietary-rag corpus-status
dietary-rag search-corpus "What special requests reduce restaurant sodium?" --active
dietary-rag search-corpus "How long can hard cooked eggs keep?" --active --documents mn_storage_chart
```

See [lifecycle guide](docs/phase3-lifecycle.md) for activation, rollback, atomic
publication, retained proof and request-scoped evidence resolution. Phase 3 is
complete; deterministic policy routing in P4.1 is next.

## Phase 4 policy and response contracts

[Policy foundations](docs/phase4-policy.md) documents the implemented P4.1–P4.6 scope gates, typed intent adapter, fixed refusal/capability messages, request/response/error contracts and pinned retrieval preparation context. Run `.venv/bin/dietary-rag check-policy "How should I store leftovers?"` to inspect routing without retrieval. Medical and calorie/weight requests are declined with professional referral; individual-food nutrient values route to Milestone 3; uncertain intent asks for clarification. Grounded answering and the HTTP API are implemented below; the browser UI is implemented through P6.6.

## Phase 5 grounded answering

[Answering foundations](docs/phase5-answering.md) documents P5.1–P5.7, the typed generator/verifier adapters, condition-aware evidence decisions, canonical citations and one bounded repair. The original Phase 5 extractive path preserves complete source conditions. Repository settings now select Groq; add `--offline` to reproduce the credential-free Phase 5 examples.

```bash
.venv/bin/dietary-rag answer-question \
  'How long can hard cooked eggs stay in the refrigerator or freezer?' \
  --documents mn_storage_chart --format text --offline \
  --report data/staging/answering/eggs.json

.venv/bin/dietary-rag answer-question \
  'What internal temperature should chicken be cooked to?' \
  --documents cdc_food_safety mn_safe_grilling_pdf --format text --offline
```

Answers require the compatible active corpus from Phase 3. JSON is the default output; `--format text` attaches complete citations beside each claim. Medical/calorie/weight and nutrient-capability responses still work without opening the corpus. Dependency failures and repeatedly invalid drafts are service errors, while evidence gaps identify the actual searched scope. The [31-case review](docs/answering-review.json) includes all six active PDFs, comparisons, partial answers, refusals and error/repair examples. HTTP endpoints are implemented below; the browser UI is implemented below.

## Phase 6.1–6.3 API and Groq

[API integration guide](docs/phase6-api.md) documents health/readiness, document availability, chat, request IDs/deadlines and typed failures. Groq uses `openai/gpt-oss-120b` with compact evidence IDs and local claim verification. Persistent request/token limits cover rolling minute, hour and day windows; `/usage` reports aggregate usage.

```bash
.venv/bin/python -m pip install --require-hashes -r requirements/api.lock
# Edit project-root .env: GROQ_API_KEY=your-key
.venv/bin/dietary-rag serve
```

The API is at `http://127.0.0.1:8000`; `/docs` has the schema. `serve` and `answer-question` load `.env`, with exported variables taking precedence. Local defaults allow 60 calls/hour, 300/day, 30,000 tokens/hour and 100,000/day, with a 1,024-token completion cap. Adjust `config/settings.yaml` to your account's limits. Use `--offline` for credential-free operation. The browser UI is available at the server root.

## Chat UI and comparisons (P6.4–P6.6)

Restart `.venv/bin/dietary-rag serve`, then open **http://127.0.0.1:8000/**.
Choose all sources, one document, or multiple documents to compare. Completed
validated answers keep each source's claims and adjacent HTML/PDF citations
separate. Partial evidence, refusal, clarification and service failures stay visible.
The footer links to a tabbed Diagnostics page for service status, source availability, hourly/daily usage limits, and API debugging. The chat keeps answers, comparisons, citations, and evidence gaps. Previous turns are not resent to Groq.

The HTML/CSS/JavaScript client in `web/` needs no build step. Same-origin serving,
text-only rendering, reviewed citation URLs and CSP protect the interface from
active source markup. See the [UI guide](docs/phase6-ui.md) and [eight-flow real-corpus review](docs/ui-review.json)
for behavior, concurrency handling, desktop/mobile inspection and verification.
634 Python tests and 42 UI tests passed; Phase 7 evaluation and operation remain.
