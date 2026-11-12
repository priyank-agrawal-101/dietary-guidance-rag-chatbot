# Deployment plan: Streamlit learning project

**Created:** November 12, 2026. **Status:** local implementation and verification complete; Community Cloud deployment reported by the user. Linux CI and cloud performance verification pending.

Implementation and runnable commands: [Streamlit deployment guide](streamlit-deployment.md).
A small reviewed canonical text bundle is tracked in Git; native indexes and frozen
selection proof are regenerated once per host configuration during cold startup.

Deploy the dietary-guidance chatbot through Streamlit while preserving its existing
policy checks, retrieval, evidence assessment, canonical citations, Groq generation,
source comparisons and diagnostics. Start with a Streamlit Community Cloud experiment.
Use persistent hosting if the experiment cannot satisfy memory or storage requirements.
Acceptance remains the user-authorized **Human–AI assisted learning-project gate**.
Independent human review or suitability for actual users is not claimed.

## 1. What “the entire project” includes

| Component | Deployment treatment |
| --- | --- |
| Chat and source selection | Native Streamlit interface using the existing Python service |
| Policy, hybrid retrieval and answer validation | Reuse `PolicyRuntime`, `ActiveRuntime` and `AnswerService` |
| Citations, comparisons and response states | Render validated responses without changing claims or source scope |
| Groq generation and budgets | Server-side credentials; shared usage reservations across sessions |
| Chroma, canonical SQLite evidence and lifecycle records | Verified deployment artifacts; active writable working copy |
| BGE-small and historical E5 tokenizer | Exact separately pinned files, prepared before serving |
| Acquisition, HTML/PDF parsing, OCR, normalization and indexing | Existing administrative CLI, executed before release or during maintenance |
| Evaluation, refresh, promotion, backup and rollback | Retained reports and administrative deployment workflow |
| Existing FastAPI, browser UI and API endpoints | Retained in the repository; separately hosted when these endpoints must remain accessible |

Community Cloud launches a Streamlit application. It does not automatically publish
the current FastAPI server, `/chat`, `/docs`, or the existing `web/` site. A native
Streamlit adapter can run the complete **chat runtime** in one process. The full
build/operations toolchain remains available to administrators outside the chat UI.
Do not run scraping, OCR, corpus rebuilding or a second web server on every rerun.

## 2. Hosting choices

| Route | What runs where | Storage and operational fit |
| --- | --- | --- |
| A. Community Cloud, all-in-one demo | Streamlit and the shared RAG service run together; reviewed text is packaged beforehand and a native index is validated at cold startup | First experiment. Treat local disk, usage history and caches as disposable across restarts/redeployments. Measure resource fit. |
| B. Streamlit on a persistent Linux host | Streamlit, runtime artifacts and CLI operations share a host with a persistent volume | Best match when the entire project's persistent stores and maintenance must reside together. |
| C. Community Cloud UI + persistent FastAPI backend | Streamlit calls a separately hosted existing API; model, corpus and ledger stay on the backend | Fallback when Cloud memory is insufficient or preserving API endpoints is required. Requires backend authentication and separate hosting. |

**Sequence:** prototype A locally and in Linux CI, then try Community Cloud. Select B
or C if durable usage accounting or resource requirements cannot be met there.
Do not claim that an all-in-one Community Cloud demo provides durable operations.
For route C, the browser talks to Streamlit; Streamlit makes authenticated HTTPS
calls to the backend. `localhost:8000` on the user's laptop is not a hosted backend.

## 3. Repository findings and prerequisites

- The application currently serves FastAPI plus HTML/JavaScript; no Streamlit entry
  point or dependency exists in `pyproject.toml`.
- `requirements/embeddings.lock` is explicitly for **macOS ARM64 / Python 3.13**.
  Community Cloud runs Linux. Generate and test a separate Linux lock rather than
  using the current wheel hashes there. The other locks also need compatibility checks.
- Initial CI target: Linux x86_64, CPU-only inference, Python 3.13. Confirm that
  Community Cloud offers this Python version and architecture. If it requires a
  different target, resolve and verify that target before deployment.
- The local model directory occupies about **130 MB**, tokenizer files about
  **944 KB**, and the `learning-v0.1.1` bundle about **16 MB**. These are measured
  disk sizes, **not RAM requirements**. Measure peak RAM, build size and cold-start
  time on Linux and compare with the current host's actual resource allocation.
- `data/` is Git-ignored. A GitHub checkout alone will not contain the active corpus,
  tokenizer or model weights. Packaging installs likewise do not include them.
- Lifecycle records contain absolute paths. A copied local `active.json` cannot
  simply be reused on the host. Revalidate and activate the working snapshot there.
- The published `learning-v0.1.1` bundle predates the latest UX and microwave fixes.
  Prepare evidence bound to the intended deployment commit; do not present the old
  reproduction report as verification of new code or Linux support.

## 4. Proposed files

These are files to implement, not commands or modules that already exist.

```text
streamlit_app.py                      # Small entry point
src/dietary_rag/ui/streamlit_app.py    # Chat, sources and diagnostics
src/dietary_rag/deployment/bootstrap.py
config/deployment-artifacts.json      # URLs/versions, sizes, SHA-256 and manifests
config/settings.streamlit.yaml        # Deployment paths and conservative budgets
requirements/streamlit.in             # Linux runtime dependency inputs
requirements/linux-streamlit.lock     # Complete tested CPU-only dependency lock
requirements.txt                     # Cloud-recognized dependency entry file
.streamlit/config.toml                # Nonsecret UI/server configuration
.streamlit/secrets.toml.example       # Placeholder keys only
scripts/reproduce_streamlit.py        # Linux build/bootstrap/evaluation smoke checks
tests/integration/test_streamlit.py
.github/workflows/streamlit-linux.yml
docs/streamlit-deployment-review.json
```

Add `.streamlit/secrets.toml` and any new runtime-cache directory to `.gitignore`.
Preserve the existing macOS locks and deployment launcher.

Root `requirements.txt` should select the Linux runtime lock, with hash checking
verified in CI and the actual Cloud installer. Do not include all development/OCR
dependencies unless the hosted process needs them. Select CPU-only PyTorch wheels
explicitly so Linux installation does not pull unnecessary CUDA packages.

Avoid mixing an editable `-e .` requirement with `--require-hashes`: editable installs
cannot satisfy that scheme. Use a minimal entry-point shim that imports the checked-out
`src/` package, or distribute a reproducible, hashed project wheel. Keep one authoritative
Cloud dependency entry file; `pyproject.toml` remains the package metadata source.

## 5. Implementation stages

### S1 — Establish the Linux runtime

- [ ] Resolve Streamlit and the core/API/embedding/tokenizer dependencies together
  on the selected Linux/Python target. Pin compatible versions and wheel hashes.
- [ ] Review shared-version differences from the current locks. Preserve BGE-small's
  model revision, 384-dimensional normalized vectors and hybrid retrieval configuration.
- [ ] Install the lock in a clean environment; run dependency/import checks.
- [ ] Verify pinned model/tokenizer artifacts, vector calculations, canonical evidence
  and Chroma compatibility. Do not assume macOS HNSW files are portable.
- [ ] If copied index validation fails, rebuild the paired index on Linux from the
  reviewed canonical chunk snapshot/database and rerun retrieval selection/evaluation.
  Preserve reviewed chunk identities; do not silently re-extract or change chunking.
- [ ] Record peak memory, cold/warm latency, disk usage and the tested environment.

**Gate:** the target environment opens a compatible corpus and returns cited HTML/PDF
answers offline. If it cannot fit Community Cloud resources, choose B or C before
building around unsupported assumptions.

### S2 — Build the native Streamlit interface

- [ ] Create chat input, reviewed-source selection, comparison mode and conversation
  rendering with the existing friendly UX messages.
- [ ] Pass the same validated request shape to `AnswerService.answer()`; do not create
  a second retrieval chain or prompt that bypasses policy/evidence validation.
- [ ] Preserve every response state: answer, source gap, unrelated/medical scope,
  structured nutrient lookup unavailable, clarification and dependency/service errors.
- [ ] Render source names, original claim text, canonical links and PDF page references.
  Keep raw IDs, full scope details and technical gaps in collapsed diagnostics.
- [ ] Use `st.session_state` only for that visitor's conversation and source choices.
  Never cache chat responses, questions or selections globally.
- [ ] Use `st.cache_resource` for a shared runtime/service and global capacity guard,
  keyed by deployment configuration and artifact version. Shared resources must be
  thread-safe. Preserve `ActiveRuntime`'s complete pinned-corpus lock.
- [ ] Enforce bounded calls across sessions, provider deadlines and usage reservations.
  Direct Python calls do not inherit FastAPI's request-slot middleware. A cancelled
  waiter must not free capacity while its worker is still running. Never replay a
  Groq call automatically during a Streamlit rerun.
- [ ] Render untrusted source/question content as inert text and allow only reviewed
  citation URLs. Do not use raw HTML for generated or scraped content.

**Gate:** source selection remains exact, session conversations stay isolated, and
reruns do not duplicate paid/provider calls. Native Streamlit is an adapter around
the established answering service.

### S3 — Bootstrap verified deployment artifacts

- [ ] Publish a versioned artifact set containing the paired snapshot, retained
  selection/freeze/held-out proof, matching configuration and model/tokenizer manifests.
  Keep original raw files and OCR/build evidence in the administrator's artifact archive.
- [ ] Supply the pinned model/tokenizer files separately. Verify redistribution/license
  requirements when hosting them; source download permission is not a blanket right
  to republish every raw PDF or page.
- [ ] Store artifact URLs and trusted hashes in deployment configuration. Fetch bounded
  sizes, verify checksums before extraction, reject archive path traversal/symlinks,
  and stage installation atomically under a single bootstrap lock.
- [ ] Preserve the immutable downloaded bundle. Validate and serve a writable snapshot
  copy: opening Chroma can change SQLite/index files even during validation.
- [ ] Pass explicit `model_dir`, `tokenizer_dir`, manifests and lifecycle paths to
  `ActiveRuntime`. Changing `DIETARY_RAG_DATA_DIR` alone does not relocate all its
  default model/tokenizer paths under the current implementation.
- [ ] Activate the candidate using retained proof and the host's real paths, never by
  editing pointer/release JSON. Serve only after validation succeeds.
- [ ] Make subsequent startup idempotent. A corrupt/incomplete download stays unready;
  an already valid installation is not rebuilt on every session or rerun.
- [ ] Prepare artifacts before setting Hugging Face/Transformers offline mode. Normal
  answering must not acquire new sources or download model revisions implicitly.

Existing activation command, with deployment-specific paths supplied explicitly:

```sh
dietary-rag validate-index --snapshot-dir WORKING_SNAPSHOT
dietary-rag activate-corpus --snapshot-dir WORKING_SNAPSHOT \
  --retrieval-config config/retrieval-hybrid.json \
  --selection-report RETAINED_PROOF/selection.json \
  --regressions RETAINED_PROOF/regressions.json \
  --heldout RETAINED_PROOF/heldout.json \
  --freeze RETAINED_PROOF/freeze.json \
  --lifecycle-dir HOST_LIFECYCLE_DIRECTORY
```

`WORKING_SNAPSHOT`, `RETAINED_PROOF` and `HOST_LIFECYCLE_DIRECTORY` are placeholders.
Pass explicit model/tokenizer CLI options when using nondefault cache paths. After
activation, construct the hosted runtime using that same lifecycle directory.

**Gate:** a clean host can recover all required artifacts, verify them and activate
the correct corpus without relying on this laptop or rewriting trusted identities.

### S4 — Configure secrets, usage and diagnostics

- [ ] Read `GROQ_API_KEY` server-side from `st.secrets` and pass it explicitly through
  `build_answer_service(..., environ=...)`. Never send it to a browser or report.
- [ ] Retain conservative shared call/token limits and the existing reservation ledger.
  Local Community Cloud SQLite history is not guaranteed to survive restart/redeploy.
  Do not claim it preserves cross-restart budgets.
- [ ] For durable accounting, choose persistent route B/C or implement a tested remote
  ledger with atomic reservations. If an ephemeral accounting mode is proposed for a
  solo Cloud experiment, document its changed semantics explicitly; it is not the
  existing durable-budget contract. Provider limits are an additional constraint.
- [ ] Make missing-key behavior helpful: sources and scope replies still work, and
  live generation reports unavailable. Provide an explicit offline test mode.
- [ ] Add a diagnostics page for source/version readiness, aggregate timings, response
  counts and usage. Direct service calls must register the existing metrics explicitly;
  FastAPI middleware is not running in route A/B's Streamlit-only process.
- [ ] Keep routine logs free of raw questions, credentials and generated responses.

Example **secret names only**, to enter in Cloud's Advanced settings:

```toml
GROQ_API_KEY = "replace-in-host-secret-settings"
# Route C only:
RAG_API_BASE_URL = "https://your-backend.example"
RAG_API_TOKEN = "replace-in-host-secret-settings"
```

Route A/B should not require a backend URL. Route C must authenticate the backend
and keep the backend's provider key off the Streamlit frontend host if unnecessary.

### S5 — Validate and create deployment evidence

- [ ] Extend reproduction tooling for Linux. The existing
  `scripts/reproduce_release.py` explicitly rejects non-macOS-ARM64 environments;
  it cannot establish Linux support unchanged.
- [ ] Run the unchanged frozen 71-case evaluation against the target's active pin,
  plus exact egg, microwave, cooking-oil and unrelated-question regressions.
- [ ] Record new results bound to the target code/artifact hashes. Complete the
  authorized assisted review for these results; disclose changed retrieval/answers.
  Preserve the original score thresholds and previous reports.
- [ ] Add Streamlit interaction tests, two-session isolation checks, concurrency,
  double-submit/rerun protection, deadlines, missing credentials and provider outage.
- [ ] Test cold bootstrap, warm rerun, wrong hashes, corrupt manifests, changed artifact
  version, interrupted download and restart recovery. Verify fail-closed behavior.
- [ ] Test source links/PDF pages, comparisons, actual gaps and collapsed diagnostics.
- [ ] Record the supported Linux configuration and full hosted runtime smoke results.
  Full ingestion/OCR checks run in an administrative environment with Tesseract;
  omitting OCR from the chat container does not establish OCR deployment support.

**Gate:** artifacts, Linux reproduction, assisted learning acceptance and runtime/UX
checks pass. The hosted diagnostics identifies the deployed commit and corpus version.

### S6 — Deploy the Community Cloud experiment

After S1–S5 pass:

1. Commit the Streamlit adapter, Cloud dependency entry, nonsecret settings and tested
   artifact references to the GitHub branch chosen for deployment.
2. In Community Cloud, create an app and select the repository, branch and root
   `streamlit_app.py` entry file. Choose the Linux-tested Python version explicitly.
3. Enter secrets in Advanced settings; do not commit `.env` or real `secrets.toml`.
4. Deploy and inspect build logs for dependency resolution, CPU-only runtime and
   successful artifact verification/activation.
5. Open the generated app URL. Verify source availability, a cited HTML answer,
   a PDF answer, source comparison, the supplied microwave/egg/oil questions and
   unrelated-topic handling. Verify missing-key/offline behavior separately.
6. Test two simultaneous sessions, a warm rerun and a host restart/rebuild. Record
   startup time, peak memory and actual persistence behavior.
7. Record the final URL, deployed commit, artifact checksums, dependency lock identity,
   limitations and smoke outcomes in `docs/streamlit-deployment-review.json`.

For local adapter testing **after it is implemented**:

```sh
python -m streamlit run streamlit_app.py
```

The default local Streamlit URL is normally `http://localhost:8501/`; the current
FastAPI development URL remains `http://127.0.0.1:8000/` until the new adapter exists.
Choose app visibility appropriate to this learning experiment using the host's
available sharing controls. No public deployment is performed by creating this plan.

## 6. Whole-project operations and recovery

Run acquisition, native extraction, OCR and source review in the administrator's
existing environment or a separately validated Linux build image. Refresh only the
approved source IDs. Changed content must pass review, normalization, chunking,
retrieval selection and evaluation before a new artifact version is published.

Keep a last-known-good code commit, configuration, dependency locks, artifact set
and retained proof. For rollback, restore matching code and artifact references,
then revalidate/activate the working snapshot or use the existing generation-checked
rollback operation. Do not mix old proof with new artifact bytes.

On persistent route B/C, back up canonical stores, lifecycle records, models and the
usage ledger while readers/writers are stopped or through a verified consistent
backup procedure. Route A must reconstruct disposable caches from verified artifacts;
it must not promise recovery of unpersisted usage history or browser conversations.

If all-in-one Cloud deployment fails its resource gate, use route C to keep model,
SQLite and Chroma workloads on a persistent backend, or route B to host the entire
Streamlit project on a persistent volume. Do not drop dense retrieval, reduce
coverage, change chunking or bypass policy validators merely to make hosting fit.

## 7. First implementation milestone

Complete **S1, S2 and S3** first: a Linux-tested Streamlit adapter that boots verified
artifacts and answers offline using the existing service. Then configure Groq and
durable usage handling, complete acceptance evidence, and try Community Cloud.

This order establishes feasibility before depending on a hosted URL or persistent
operations that the chosen service may not provide.

## References

Official Streamlit documentation checked November 12, 2026:

- [Community Cloud deployment](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app)
- [Dependencies and Linux system packages](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies)
- [Cloud secret management](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)
- [Shared resource caching and thread safety](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.cache_resource)

Repository references: [operations runbook](operations-runbook.md),
[dependency locks](../requirements/README.md), [assisted review gate](review-gate-revision.md),
[UX verification](ux-improvements.md) and [microwave regression](microwave-paraphrase-review.json).
