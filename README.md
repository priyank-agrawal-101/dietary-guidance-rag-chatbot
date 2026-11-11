# dietary-guidance-rag-chatbot

Build a prototype for a US audience using **HTML pages and PDF documents**, prioritizing authoritative government guidance. Every factual claim must come from retrieved evidence and carry a citation, including page references for PDFs. The service reports evidence gaps and declines medical advice and calorie/weight targets.

The [source registry](config/sources.yaml) records 68 inspected candidate URLs and retains all 20 earlier IDs. The [source review](docs/sourceReview.md) and [P1.7 coverage review](docs/phase1-coverage.md) document access, audience, currency, rights, alternate formats and 12 verified official PDFs; 9 PDFs are approved with exclusions. The default selection has **16 eligible consumer families: 10 HTML assets and 6 PDFs**, all imported and byte-verified under the current review snapshot. This meets the initial acquisition coverage target; extraction/indexing gates remain separate.

See the [problem statement](docs/problemStatement.md) and [architecture](docs/architecture.md) for HTML/PDF extraction, OCR fallback, cleaning, normalization, chunking, indexing and citation requirements. Phase 0 provides the Python package, settings, contracts and identity helpers. P1.1/P1.2 add registry eligibility, immutable raw-byte artifacts and SQLite provenance storage. **P1.3–P1.6 implement reviewed-file imports, actual-format inspection, bounded HTTPS fetching and acquisition commands/reports.** Extraction and the chatbot remain planned. Downloaded source snapshots, acquisition outputs and inspection renders are ignored by Git.

Follow the [phase-wise implementation plan](docs/implementation-plan.md) for build dependencies, tasks, deliverables, learning outcomes and completion gates.

Use Python 3.11 or later; this machine has `python3.13`, while its default `python3` is 3.9. From the repository root:

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements/dev.lock
python -m pip install --require-hashes -r requirements/acquisition.lock
python -m pip install --no-deps --no-build-isolation -e .
dietary-rag check-settings
dietary-rag check-registry --include-decisions
python -m pytest
python -m ruff check src tests
python -m ruff format --check src tests
```

See [Phase 0 foundations](docs/phase0-foundations.md) for configuration precedence, synthetic contract examples, identity/version rules, dependency groups and wheel installation. Core, development and acquisition dependencies are hash-locked; full extraction, API, model and OCR extras are reserved for later phases. Acquisition does not require model/API credentials.

See [P1.1/P1.2 corpus foundations](docs/phase1-corpus-foundation.md) for consumer selection reasons, original-byte storage, SQLite usage and provenance checks. The registry check is read-only; repository initialization and writes require explicit API calls. It does not import the review cache or activate a corpus.

See [P1.3–P1.6 source acquisition](docs/phase1-acquisition.md) for a beginner-friendly explanation of each task, import recipes, outcomes, provenance and verification. To fetch two explicitly selected sources:

```sh
dietary-rag fetch-sources mn_cold_storage mn_cross_contamination_pdf \
  --report data/staging/acquisition-reports/live-two-source-fetch.json
```

Matching reviewed files are stored for extraction; changed files are retained as `needs_review` without replacing approved versions. This command prints a JSON report and returns exit code 1 if review is required. **Phase 1 is complete through P1.7.** Phase 2 extraction is next.

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
