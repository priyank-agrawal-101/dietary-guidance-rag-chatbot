# Evaluation, release and operations runbook (P7.4–P7.7)

This deployment targets **macOS ARM64 and CPython 3.13**, the platform covered by
`requirements/embeddings.lock`. It runs one foreground API process with embedded
Chroma, SQLite provenance and persistent Groq usage budgets. Other platforms need
separately resolved/tested model-runtime locks. Do not use multiple workers or
reload mode. Automated evaluation and the user-authorized Human–AI assisted learning review pass. Local
development activation is not release approval.

## Install and start

Use a checkout containing `config/`, `requirements/`, `web/` and `docs/`. Python
packages do not contain source registry, model weights or corpus artifacts.

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes \
  -r requirements/core.lock -r requirements/api.lock \
  -r requirements/embeddings.lock -r requirements/chunking.lock
.venv/bin/python -m pip install --no-deps PATH_TO_REPRODUCED_WHEEL.whl
```

For ingestion/tests, also install the acquisition/HTML/PDF/OCR/dev locks described
in [dependency setup](../requirements/README.md). Install Tesseract with English
data separately. Prepare the separately hash-pinned artifacts explicitly once:

```sh
.venv/bin/dietary-rag prepare-tokenizer
.venv/bin/dietary-rag prepare-embeddings
```

Place a reviewed immutable paired snapshot and its validated lifecycle records at
`data/staging/indexes/` and `data/active/`. Preserve the original absolute snapshot
path when restoring on the same machine. On a new machine/path, **revalidate and
activate the snapshot at its new path** using the retained selection proof and
case files, rather than editing the old pointer or release JSON. Copy matching
config/model/tokenizer manifests. A release bundle provides `snapshot/`, `proof/`
and configuration but does not include model weights or original raw files.

Set `.env` with `GROQ_API_KEY` for Groq (`openai/gpt-oss-120b`), or use offline mode.
Exported variables override `.env`. Keep secrets out of reports and bundles.

```sh
./deploy/start-service.sh --host 127.0.0.1 --port 8000
# Local deterministic mode:
./deploy/start-service.sh --offline --host 127.0.0.1 --port 8000
```

Use a local supervisor to restart the foreground command. It does not daemonize.
`DIETARY_SERVICE_PYTHON` selects an installed environment. CLI binds localhost by
default; this runbook covers local deployment, not a public authenticated service.

Check `/health/live` for process liveness, `/health/ready` for corpus/model
configuration, `/documents` for actual availability, `/usage` for persistent rolling
Groq budgets, and `/metrics` for process-local aggregates. Readiness validates local
compatibility and key configuration; it does not prove Groq is reachable.
Groq receives the question and compact retrieved evidence selections; local code
validates claims and creates citations. Refusals/capability states spend no Groq call.
Request timeout, provider outage and corpus failure remain separate from evidence gaps.

## Stores and observability

Retain `data/staging/raw`, `provenance.sqlite`, normalization/chunk artifacts and
reviews, `chunks.sqlite`, immutable paired indexes, `data/active` including previous
release/proof, local model/tokenizer caches and `data/usage/groq.sqlite`. Paths are
configured by `config/settings.yaml`; `DIETARY_RAG_DATA_DIR` overrides the data
root. Model/tokenizer caches remain under the project root unless explicitly
configured through the administrative CLI options.

Stop the service and ingestion before copying persistent stores for backup so
SQLite/Chroma files form a consistent copy. Keep the retained previous snapshot;
there is no automatic garbage collection. Restore all paired files and exact
matching configuration, then run readiness and cited HTML/PDF smoke checks.
Never delete usage history merely to reset provider budgets.

`/metrics` exposes worker-completed chat-state counts and count/total/max seconds
for policy, corpus opening, retrieval, evidence assessment, generation, validation
and citations. Stage timers include failed stages and each repair attempt.
Counters reset on restart and are scoped to one process. Late workers can finish
after the HTTP deadline; these counters describe worker outcomes, not completed
HTTP deliveries. Requests rejected before the worker are not counted. No questions,
claims, quotes, URLs, document IDs or request IDs are metric labels. CLI inspection
reports do contain explicit question/evidence data; create them only for review.
Routine Uvicorn access logs are disabled. Refresh reports include per-source timing
and administrative outcomes, never chat questions.

## Conditional refresh and changed metadata

Refresh is an administrative operation, separate from chat. Select reviewed IDs
or `--all-eligible`:

```sh
.venv/bin/dietary-rag refresh-sources mn_cold_storage \
  --report data/staging/refresh/review.json
.venv/bin/dietary-rag refresh-sources --all-eligible \
  --report data/staging/refresh/all.json
```

The first successful download records ETag/Last-Modified when supplied. Later
requests send bounded validators only for the exact URL and only after hashing
cached approved bytes. Redirects drop validators. A 304 must match the verified
cache and URL; original acquisition time is not relabeled as a new retrieval.
Unconditional 200 responses pass the same original-byte inspection/provenance
pipeline as ordinary acquisition. Changed bytes/URLs/layout stay `needs_review`;
no approved document version is created for changed bytes. Cache corruption or
fetch failure preserves the active pointer and prior usable refresh state.
The refresh state/report is atomically written under an administrative lock.

Source metadata fingerprints include rights, eligibility, dates, audience and
exclusions. Metadata changes disable conditional reuse and report
`metadata_changed`; newly ineligible records report `blocked`. Review-required
state persists over repeat refreshes. Refresh **never** publishes or rolls back.
Exit 0 means every source was unchanged; exit 1 means failure or review work exists.

For any content/metadata/eligibility change, inspect stored acquisitions, original
HTML/PDF, rights and exclusions; revise `config/sources.yaml` only after review.
Run extraction, normalization and exact output review, then chunk and build a
complete new candidate. Unchanged approved raw/parser outputs can be reused only
when hashes/configuration/exclusion masks and review receipts still match. Metadata
changes invalidate eligibility/citation decisions even if text is identical.
Do not reuse an old source review receipt against changed bytes.
After reviewing and activating the rebuilt snapshot, archive the refresh state and
remove only the affected source's entry from `data/staging/refresh/state.json` while
no refresh command is running. This clears its persistent review flag and starts a
new validator baseline; it does not itself approve or activate evidence.

## Build, OCR and candidate validation

The acquisition/extraction/review recipes are in [HTML](phase2-html-extraction.md),
[PDF](phase2-pdf-extraction.md), [normalization](phase2-normalization.md) and
[chunking](phase2-chunking.md). The frozen reviewed current corpus is not recreated
by fetching current URLs and trusting them automatically.

```sh
.venv/bin/dietary-rag extract-html --all-eligible --report data/staging/html-report.json
.venv/bin/dietary-rag extract-pdf --all-eligible --report data/staging/pdf-report.json
.venv/bin/dietary-rag normalize-extractions \
  --reports data/staging/html-report.json data/staging/pdf-report.json \
  --review-file data/staging/normalization-review/receipts.json \
  --report data/staging/normalization-report.json
.venv/bin/dietary-rag chunk-sources \
  --normalization-report data/staging/normalization-report.json \
  --report data/staging/chunk-report.json
```

The review-file path represents the exact receipts created during source/output
review; it is not a bundled blanket approval. If receipts are absent/mismatched,
produce pending normalized output first, inspect it and create exact review
receipts as documented. Build an index using the **snapshot ID emitted by the new
chunk report**, then validate it:

```sh
.venv/bin/dietary-rag build-index --chunk-snapshot YOUR_REVIEWED_CHUNK_SNAPSHOT_ID \
  --report data/staging/index-build-report.json
.venv/bin/dietary-rag validate-index --snapshot-dir YOUR_CANDIDATE_DIRECTORY
.venv/bin/dietary-rag evaluate-retrieval --snapshot-dir YOUR_CANDIDATE_DIRECTORY \
  --output-dir data/staging/retrieval/new-review
```

Retain BGE-small, hybrid retrieval and 350-token target/50-token overlap until
measured failures justify a reviewed change. Complete embedding inputs stay within
512 tokens; atomic tables/qualifiers are retained, not truncated. Updated corpora
need newly reviewed frozen evidence labels; do not edit old frozen receipts to
pretend they apply to a different snapshot.

For OCR use the labeled scan/mixed fixture procedure in [OCR extraction](phase2-ocr-extraction.md):
`python scripts/build_ocr_fixture.py`, then `extract-ocr --file ... --document ...
--plan ... --report ...` with its exact byte-bound plan. Review page/region locations,
confidence and numeric/unit corrections. Quarantined OCR remains unindexed;
fixture pages never contribute to consumer-family/PDF quotas. Full reproduction
requires real Tesseract checks without skipped tests.

## Evaluation, publication and rollback

```sh
.venv/bin/python scripts/evaluate_release.py --output data/staging/evaluation/results.json
.venv/bin/python scripts/reproduce_release.py --output docs/reproduction-review.json
```

The reproduction runner builds a wheel, creates an isolated venv, installs all nine
hash locks from PyPI (or offline with `--wheelhouse`), runs package/settings/registry
checks, the full Python suite without skips, and offline real-active-corpus API
checks including cited HTML and PDF answers. It writes separate step logs in a
temporary directory. It never inherits Groq keys or reads `.env` for API smoke.
A passing environment reproduction does not fix semantic evaluation failures.
The original byte extraction and independent human reviews remain prerequisites;
reproduction validates their retained snapshot rather than reapproving fresh sources.

For an independent-human release, complete the worksheet for the **exact** results hash with a real reviewer,
boolean verdicts/notes for every case, and `status: completed`. Review against the
rubric in [evaluation](phase7-evaluation.md). Evaluate fixes without lowering the
prespecified 100% structural/policy, 95% conditions/grounding and zero-critical-error
thresholds. Then evaluate the release gate:

```sh
.venv/bin/python scripts/check_release.py \
  --results data/staging/evaluation/results.json \
  --human-review data/staging/evaluation/results.human-review.json \
  --output data/staging/release-gate.json
# Only passing gates can produce a new local immutable bundle:
.venv/bin/python scripts/check_release.py \
  --results data/staging/evaluation/results.json \
  --human-review data/staging/evaluation/results.human-review.json \
  --publish-dir data/releases/APPROVED_VERSION \
  --output data/staging/release-gate.json
```

The gate recomputes checks from responses instead of trusting an automated success
flag. It binds cases/results/human review and active release IDs, checks current
indexed eligibility, requires full human review and passing clean reproduction bound to the current implementation hash.
The bundle contains only the snapshot, retained proofs, config, locks, evaluation,
reproduction and runbook, plus per-file SHA-256 checksums. Original raw sources and
model weights are managed separately under their rights/license reviews. Verify
checksums before installation. Bundling does not activate a new corpus.

Development promotion/rollback remain the existing explicit, revalidated operations:

```sh
.venv/bin/dietary-rag corpus-status --report data/staging/before-promotion.json
.venv/bin/dietary-rag activate-corpus --snapshot-dir YOUR_CANDIDATE_DIRECTORY \
  --retrieval-config YOUR_PAIRED_RETRIEVAL_CONFIG \
  --selection-report YOUR_PASSING_SELECTION_REPORT \
  --expected-generation CURRENT_GENERATION \
  --report data/staging/promotion.json
.venv/bin/dietary-rag rollback-corpus --expected-generation CURRENT_GENERATION \
  --report data/staging/rollback.json
```

On relocation use bundle `snapshot/`, `config/retrieval-hybrid.json`,
`proof/selection.json`, and explicit `--regressions proof/regressions.json
--heldout proof/heldout.json --freeze proof/freeze.json` during activation.
Generation checks reject competing/stale publishers. A failed validation leaves
active state intact; rollback revalidates the retained previous pair. Initial
activation has no previous release and cannot roll back. Retain before/after
reports, release proof and both snapshot directories. Promotion tests should use
an isolated lifecycle directory; don't churn the user's active pointer merely to
demonstrate rollback.

## Current release decision

[Release report](release-review.json) records the actual checks and blockers.
The 71-case suite now scores 71/71 after fixing the 25 baseline answerability failures. The user-authorized learning-project Human–AI assisted gate is complete; independent human review is not claimed. All learning-scope gates pass, including clean reproduction. P7.7 publishes the
approved report and immutable local bundle at `data/releases/learning-v0.1.1`
using the command in the gate revision. The release scope is learning-only.
No scientific/medical experiments, nutrient database, personalized plans, live
answer-time web search, persistent chat memory or arbitrary uploads are added.

Verification on the supported macOS ARM64/Python 3.13 target: 672 Python tests passed in an isolated hash-locked environment using the installed wheel; offline active-corpus API checks passed for readiness, documents, cited HTML/PDF answers, refusal and aggregate metrics. Conditional refresh proof and lint/format checks passed. The original active pointer was retained.

The learning-project reviewer exception is documented in [the gate revision](review-gate-revision.md). Use `scripts/check_release.py --learning-project` for that scope; the default command still requires independent human review. The completed worksheet now records 71/71 condition completeness, resolves the three prior findings, and retains AI authorship and user authorization explicitly. Learning bundles are labeled `learning_project_only`.

Validate or activate a **working copy** of a published bundle snapshot. Opening
Chroma can change its SQLite/index files even during validation. Preserve the
immutable bundle and verify its `checksums.json` before copying.
