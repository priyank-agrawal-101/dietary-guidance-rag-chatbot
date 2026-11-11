# Phase 0 Foundations

Phase 0 implements package setup, validated nonsecret configuration, shared provenance
contracts and deterministic identity helpers. Acquisition, parsers, OCR, embeddings,
storage, scope policy, generation and HTTP endpoints remain future tasks in the
[implementation plan](implementation-plan.md).

## Environment and dependency groups

Use Python 3.11 or later. On the current development machine, `python3` is 3.9 and
`python3.13` is available. Choose the supported interpreter explicitly when creating
the environment.

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements/dev.lock
python -m pip install --no-deps --no-build-isolation -e .
```

The core lock contains Pydantic and PyYAML plus their dependencies. Development adds
pytest, Ruff and packaging/lock tools. For runtime-only installation, install
`requirements/core.lock` and a built wheel with `--no-deps`; a wheel does not need a
build backend in that environment. See [dependency-lock instructions](../requirements/README.md).

| Optional extra | Planned use | Additional setup |
| --- | --- | --- |
| `ingestion` | HTTPX, Beautiful Soup/lxml and pypdf/pdfplumber | Introduce and lock in Phases 1–2. |
| `api` | FastAPI/Uvicorn | Introduce and lock for the HTTP service. |
| `embeddings` | Sentence Transformers and Chroma | Large model/runtime dependencies; pin model revision when building an index. |
| `ocr` | pytesseract and Pillow | Requires separately installed Tesseract and Poppler; introduce during OCR tasks. |

Future OCR setup should verify `tesseract --version`, `pdftoppm -v` and installed OCR
languages. OCRmyPDF is an optional later alternative, not a Phase 0 dependency. The
extras declare intended dependencies; they do not imply their services are implemented.

## Implemented commands

Run from the repository root after installation:

```sh
dietary-rag check-settings
python -m dietary_rag check-settings
python scripts/validate_source_registry.py
python -m pytest
python -m ruff check src tests
python -m ruff format --check src tests
python -m build --wheel --no-isolation
python -m pip check
```

`--config` and `--project-root` allow explicit configuration outside the current working
directory. `check-settings` validates and displays only nonsecret configuration; it does
not create directories, fetch sources, open the registry, download models or start servers.
Invalid configuration returns exit code 2. The existing registry script separately checks
acquisition metadata. Its `--check-cache` option requires pypdf and the local review cache;
neither is required for Phase 0 baseline validation.

Fetch/import, extract, OCR, chunk, build-index, chat and serve commands remain planned.
They are not exposed as working placeholder commands.

## Configuration behavior

`load_settings` uses **defaults → YAML → allowlisted environment overrides**. Its caller
passes environment values explicitly; the CLI passes the process environment. Relative
data/registry/config paths resolve against the explicit project root, and absolute paths
remain absolute. Loading checks values without creating runtime artifacts.

Allowed overrides are `DIETARY_RAG_DATA_DIR`, `DIETARY_RAG_SOURCE_REGISTRY`,
`DIETARY_RAG_LOG_LEVEL`, `DIETARY_RAG_MODEL_PROVIDER` and `DIETARY_RAG_MODEL_NAME`.
`.env.example` documents them. Export values explicitly; `.env` is not loaded automatically.
`DIETARY_RAG_MODEL_API_KEY` is reserved for later adapters and is not read or displayed
by the settings loader. Unknown YAML keys are rejected, so credentials cannot be added
to the printable settings model. Validation messages omit submitted input values.

Generation defaults to `unconfigured`; `local`/`hosted` require a nonempty model name.
The embedding revision is deliberately null until Phase 3 pins a real model revision.
Token sizes are starting configuration, not proof that a particular tokenizer fits them.
Positive limits, bounded retries and overlap smaller than chunk size are validated.

## Shared contracts

Contracts are in `dietary_rag.corpus.contracts`; the extraction interface is in
`dietary_rag.ingestion.interfaces`. Models forbid extra fields and freeze attribute
assignment. Their tuple-based block/date metadata preserves ordering without mutable
list defaults. These are structural contracts, not an implementation of source review
or runtime policy enforcement.

| Contract | Preserved information and invariants |
| --- | --- |
| `SourceDates` | Nullable publication year, separately labeled edition/date observations; no year is inferred from retrieval or edition. |
| `SourceEligibility` / `RightsReview` | Reviewed decision, proposed default selection, allowed/excluded sections and document-specific reuse observations. Default selection requires approval and eligible sections. |
| `AcquisitionRecord` | Original/resolved URL, expected/detected format, explicit outcome, timezone-aware check/retrieval times, status, bytes and raw identity. Success requires correct-format content and complete acquisition provenance. |
| `DocumentVersion` | Stable source/family/version identity, title/issuer, format, dates, applicability, review decisions, hashes and PDF inspection. Default consumer proposals require US consumer scope; PDF versions require physical page count. |
| `SourceLocation` | HTML anchor or physical PDF page/printed label, bounding box, extraction method, OCR confidence and review state. Physical pages are one-based and distinct from printed labels. |
| `TableContext` / `ContentBlock` | Ordered canonical text and original locations, headings, units, headers and footnotes. Table evidence requires contextual title/columns; alternate formats do not share one evidence block. |

Acquisition results do not perform signature checks by themselves; the Phase 1 adapter
must inspect actual bytes before creating a success record. Similarly, eligibility fields
are reviewed proposals: later source/quality/policy gates determine whether a block can
enter an active consumer corpus. Cross-record relationships, page upper bounds, storage
and parser/chunk contracts are extended in their own phases.

The [example payloads](examples/phase0-contracts.json) are synthetic structural examples,
not actual nutritional guidance or a new approved government source. Tests validate them
and their linked document/block identity.

## Identity and version rules

Use `sha256_bytes` for raw bytes and `stable_id(namespace, payload, schema_version="1")`
for deterministic metadata/content identities. Stable IDs use sorted-key compact UTF-8
JSON, retain sequence order, normalize timezone-aware datetime objects to UTC, and reject
NaN or naive datetimes. Models, URLs, dates, tuples and explicit paths are supported.
Canonicalization does not remove warnings, normalize source recommendations or merge
different versions.

| Identity | Inputs to include when its implementation is introduced |
| --- | --- |
| `document_id` | Existing registry identity, stable across content changes; never regenerate from a title. |
| `family_id` | Reviewed family relationship for alternate HTML/PDF formats; remains distinct from asset identity. |
| `docv_*` | Source ID, raw hash, verified attribution/URLs/date labels, applicability/rights/eligibility and relevant parser/normalizer/exclusion-mask versions. |
| `block_*` | Document version, sequence/location, canonical text, block/table context and extraction/normalizer version. |
| Future `chunk_*` | Document/block versions, location/text and full chunker configuration. |
| Future `corpus_*` | Included reviewed versions, eligibility and full extraction/chunking/embedding configuration, including pinned model revision. |

Callers supply the complete identity payload; the generic helper does not infer missing
fields or enforce a future corpus schema. Cache paths, credentials and machine-specific
absolute roots should not enter content identity. Source-stated and acquisition dates
remain separate in storage. Equivalent unchanged payloads reproduce IDs; a changed
warning, exclusion rule, source date/rights decision or processing version must produce
the appropriate new identity. Metadata-only changes produce traceable document/snapshot
versions even when raw or extracted text hashes remain unchanged.

## Completion evidence

Phase 0 is complete when the locked environment installs, a built wheel works in a clean
runtime environment, settings load/reject invalid input correctly, shared contracts and
identities pass reviewed tests, package imports have no application/network/model side
effects, and the existing source-registry baseline passes. Record actual commands/results
in the implementation plan; do not mark later application phases complete from this setup.

### Verified on November 11, 2026

Python 3.13.16 on macOS: 44 tests passed, Ruff lint/format checks passed, settings CLI
loaded the committed configuration, and dependency compatibility checks passed. An
editable install and a wheel build both succeeded. The wheel was installed into a fresh
core-only environment and its CLI ran outside the repository working directory without
creating runtime artifacts. All package modules imported without loading model/OCR
libraries; the source validator retained the 61-candidate/20-existing-ID baseline.

No other Python version/platform was executed in this milestone. The declared Python
minimum is 3.11; verify lock installation and tests on each additional supported target.
