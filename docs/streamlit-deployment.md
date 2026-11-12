# Deploy the learning chatbot with Streamlit

The root `streamlit_app.py` runs the existing grounded Python service directly.
It includes source selection, comparisons, validated citations, offline excerpts,
friendly response states and collapsed diagnostics. The suggested-question panel
loads all 15 prompts from `docs/test-questions-fot-chatbot`: six are immediately
visible and nine are under “More questions”. Choosing one fills the chat box for
editing; only sending it requests an answer, using the current source selection. FastAPI and the original web
interface remain available through their existing launch command.

## Local launch on this Mac

Use the existing Python 3.13 environment, or create a new one:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements/macos-streamlit.lock
./deploy/start-streamlit.sh
```

Open **http://localhost:8501**. Locally, `.env` or `.streamlit/secrets.toml` can
supply `GROQ_API_KEY`. Use `.streamlit/secrets.toml.example` as the template.
Exported environment values override `.env`; Streamlit secrets override both.
The visitor-facing offline toggle and banner have been removed. Without a key,
the internal source-excerpt fallback remains available; its mode is recorded only
in the collapsed Service diagnostics panel at the bottom of the sidebar.

First startup downloads the exact hash-verified BGE model and historical E5
tokenizer from their immutable Hugging Face revisions. It verifies the packaged
canonical evidence, creates a native paired SQLite/Chroma index, evaluates all
48 frozen retrieval questions and scope checks for all three strategies, and
activates only a passing selected hybrid configuration. This can take a few
minutes. Subsequent reruns reuse the same runtime and embedding model. The loading
spinner appears only when the shared runtime cache is first populated. UI edits
and suggested-question changes do not invalidate the corpus index; changes to
canonical evidence, retrieval code or compatible model configuration still do.
Pinned model files live in a separate cache and can survive corpus-version
changes on retained disk. No source scraping or OCR runs during startup.

For local verification using the already downloaded pinned model files:

```sh
OMP_NUM_THREADS=2 TOKENIZERS_PARALLELISM=false .venv/bin/python scripts/check_streamlit.py \
  --model-seed data/staging/models/bge-small-en-v1.5 \
  --tokenizer-seed data/staging/tokenizers/e5-base-v2
.venv/bin/python -m pip install --require-hashes -r requirements/dev.lock
.venv/bin/python -m pytest tests/test_streamlit_deployment.py -q
```

The checker also evaluates the 71 frozen answer/policy cases, verifies a warm
restart, measures process memory and checks the eggs, microwave and out-of-scope
questions. It writes `.streamlit-runtime/deployment-check.json`. Seeds are
optional: cloud and CI download the pinned artifacts themselves.

## Streamlit Community Cloud

1. Commit/push the application changes, including `deployment/artifacts/corpus.tar.gz`,
   `config/deployment-artifacts.json` and the dependency locks, to your GitHub repository.
2. Check the **Streamlit deployment checks** GitHub Actions job. It installs the
   hash-locked Linux CPU runtime, verifies Torch has no CUDA dependency, tests
   Streamlit sessions, rebuilds the native index and runs the frozen evaluations.
3. In Streamlit Community Cloud, select that repository and branch. Set **Main
   file path** to `streamlit_app.py` and **Python version** to **3.13**.
4. In **Advanced settings → Secrets**, paste:

   ```toml
   GROQ_API_KEY = "your-real-key"
   ```

   Keep the key out of Git. Omitting it enables the offline demo.
5. Deploy and wait for initial source preparation. Check the startup log if it
   fails. Test source selection, a source comparison, the eggs and microwave
   questions, an unrelated political question and the displayed citations.
6. Record the assigned `https://…streamlit.app` URL and check memory/resources
   on the actual host. Streamlit's `/_stcore/health` reports web-server liveness;
   a usable source picker and a cited answer verify chat readiness.

The provided code has [recorded local macOS verification](streamlit-verification.json):
709 Python tests, 71/71 frozen answer cases, the passing selected hybrid retrieval
configuration, and real-corpus Streamlit questions and a two-source comparison. A Linux lock has been resolved,
but an actual successful Linux CI run and Community Cloud deployment are required
before claiming those hosts are verified. A historical macOS release report does
not approve this new deployment or establish Linux compatibility.

## State and learning-project limits

This is the user-authorized **Human–AI assisted learning-project gate**, with no
claim of independent human review or suitability for actual users.

Cloud local disk is disposable. The UI labels the default answer allowance as
**per app instance**: the shared SQLite ledger still atomically reserves usage
across sessions, but cloud restarts may lose its history. This demo does not
provide durable daily/hourly limits. Chat history is isolated in each visitor's
Streamlit session and limited to 20 turns. Questions and credentials are never
included in global resource cache keys or aggregate metrics. Credential rotation
uses a fingerprint to invalidate the service cache; all modes share one corpus
lock and capacity gate.

`DIETARY_STREAMLIT_CACHE_DIR` changes the writable artifact cache;
`DIETARY_STREAMLIT_USAGE_DIR` sets the accounting parent directory (the ledger is
`usage/groq.sqlite` inside it). Only set the latter to a genuinely persistent
mounted volume if durable accounting is required. Keeping the same usage directory
across artifact upgrades preserves budget history. Startup fails closed if an
already published runtime is corrupt. Do not delete the accounting directory to
repair source files. After inspecting a source corruption, remove or quarantine
only the affected version directory in the artifact cache and retry preparation.

The complete request retains its configured deadline, policy/evidence/citation
validation, shared maximum concurrency and Groq reservation limits. A widget
rerun does not automatically repeat a request. No automatic paid retries are
introduced by the UI.

## Persistent Linux hosting option

```sh
docker build -f deployment/Dockerfile -t dietary-streamlit .
docker volume create dietary-state
docker run --rm -p 8501:8501 --env-file .env \
  -v dietary-state:/state dietary-streamlit
```

The container runs as a non-root user and stores native artifacts and accounting
in `/state`. It does not expose FastAPI or run the administrative pipeline.
Docker builds have not been executed on this Mac; validate the image before use.
Run refresh/build/evaluation/release CLI operations outside the visitor interface,
then rebuild the portable bundle with:

```sh
.venv/bin/python scripts/package_streamlit_corpus.py
```

Review the resulting canonical source changes and retained frozen proof before
committing both the archive and its checksum receipt. The bundle contains reviewed
attributed text and evidence locations, not raw PDFs, images, weights, credentials,
chat logs or provider usage history. Native indexes and host-path lifecycle records
are rebuilt rather than copied from the laptop. If Community Cloud memory is
insufficient, use the persistent host or separately hosted API route in the
[deployment plan](deployment-plan.md).

## Regenerating dependencies

Linux targets CPython 3.13 x86_64, with CPU Torch; macOS has a separate local lock.
The reviewed `streamlit-constraints.txt` preserves the original runtime versions.
Use `uv` to regenerate deliberately, then rerun host checks:

```sh
uv pip compile requirements/linux-streamlit.in --constraint requirements/streamlit-constraints.txt \
  --python-version 3.13 --python-platform x86_64-unknown-linux-gnu \
  --generate-hashes --only-binary :all: --no-annotate \
  --output-file requirements/linux-streamlit.lock
uv pip compile requirements/streamlit.in --constraint requirements/streamlit-constraints.txt \
  --python .venv/bin/python --generate-hashes --only-binary :all: --no-annotate \
  --output-file requirements/macos-streamlit.lock
```

The Linux lock pins a direct official CPU wheel URL and hash for Python 3.13;
this avoids sending unrelated package lookups to the Torch index. The root
requirements file enforces wheel hashes. Parsing/OCR/test tooling is intentionally excluded from the visitor runtime;
the existing administrative dependencies remain in their original lock files.

## If source preparation takes a long time

[Recorded local cache/model verification](streamlit-performance-review.json):
713 tests, 71/71 frozen answer cases and real-corpus model reuse across questions.

A fresh or recycled cloud instance must download approximately 133 MB of embedding
weights and run the native index/retrieval checks. This is separate from answering
a question with Groq. The updated adapter avoids repeating downloads for UI
changes on retained disk, reuses the embedding model across requests and shows
the startup spinner only on a resource-cache miss. Cloud redeployments can still
discard local disk, so a completely fresh startup is slower than the previously
prepared local FastAPI service.

After committing/pushing the update, allow the app to rebuild once. If it keeps
loading without reaching the source picker, inspect **Manage app → Logs** for
download failures or memory/CPU resource errors. A web-server health check alone
does not establish corpus readiness. Cloud timings have not been measured by
the local benchmark; do not treat a local startup time as a cloud guarantee.

Answer citations use short numbered references such as `[1]`. Reviewed source
names, links and supporting quotes appear in the collapsed “Sources and supporting
passages” accordion. Internal citation identifiers remain in answer diagnostics.
