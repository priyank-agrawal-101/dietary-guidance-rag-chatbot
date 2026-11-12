# Dependency locks

`core.lock` pins and hashes the configuration/contract dependencies. `dev.lock` adds tests,
linting, build and lock-generation tools, constrained by the core lock. They were resolved
with Python 3.13; the project supports Python 3.11 or later. Verify installation on another
Python/platform before claiming compatibility beyond the tested environment.

`acquisition.lock` activates HTTPX/HTTPcore and pypdf for P1.3–P1.6, constrained by
`core.lock`. Install it in addition to `dev.lock` for acquisition tests:

```sh
python -m pip install --require-hashes -r requirements/dev.lock
python -m pip install --require-hashes -r requirements/acquisition.lock
```

Regenerate intentionally from `pyproject.toml` in the development environment:

```sh
python -m piptools compile pyproject.toml --generate-hashes --strip-extras --output-file requirements/core.lock --no-emit-index-url --no-emit-trusted-host
python -m piptools compile pyproject.toml --extra dev --constraint requirements/core.lock --generate-hashes --allow-unsafe --strip-extras --output-file requirements/dev.lock --no-emit-index-url --no-emit-trusted-host
python -m piptools compile pyproject.toml --extra acquisition --constraint requirements/core.lock --generate-hashes --strip-extras --output-file requirements/acquisition.lock --no-emit-index-url --no-emit-trusted-host
```

Review dependency changes, install the locks with `--require-hashes`, and rerun checks.
`html.lock` activates Beautiful Soup and its selector dependency for P2H.1–P2H.3,
constrained by `core.lock`. Install it alongside the development/acquisition locks to
run the HTML tests and acquisition-backed CLI:

```sh
python -m pip install --require-hashes -r requirements/html.lock
python -m piptools compile pyproject.toml --extra html --constraint requirements/core.lock --generate-hashes --strip-extras --output-file requirements/html.lock --no-emit-index-url --no-emit-trusted-host
```

The HTML extractor uses Python's built-in HTML parser; it does not require lxml,
PDF layout packages, OCR or model dependencies.

`pdf.lock` activates native parsing/rendering plus the small `pdf-dev` fixture/test
extra for P2P.1–P2P.4. It is constrained by both core and acquisition locks so their
shared pypdf version agrees. Install all four development/acquisition/HTML/PDF locks
to run the complete suite:

```sh
python -m pip install --require-hashes -r requirements/pdf.lock
python -m piptools compile pyproject.toml --extra pdf --extra pdf-dev --constraint requirements/core.lock --constraint requirements/acquisition.lock --generate-hashes --strip-extras --output-file requirements/pdf.lock --no-emit-index-url --no-emit-trusted-host
```

PDF parser/renderer imports are lazy; core settings and HTML APIs do not load them.
ReportLab is used only for portable project-created PDF fixtures. Native extraction
and rendering do not require OCR, model packages or external Poppler executables.

`ocr.lock` activates the region OCR adapter's shared PDF/Pillow dependencies,
constrained by core and PDF locks. It uses the Tesseract CLI directly; no Python OCR
wrapper, model weights or OCRmyPDF installation is required.

```sh
python -m pip install --require-hashes -r requirements/ocr.lock
python -m piptools compile pyproject.toml --extra ocr --constraint requirements/core.lock --constraint requirements/pdf.lock --generate-hashes --strip-extras --output-file requirements/ocr.lock --no-emit-index-url --no-emit-trusted-host
```

Install Tesseract and `eng` language data separately (`brew install tesseract` on
macOS, or `apt-get install tesseract-ocr tesseract-ocr-eng` on Debian/Ubuntu).
The tested engine is Tesseract 5.5.4; engine/executable/language-data hashes are
recorded per extraction. System binaries are not pinned by the Python lock.
The real-engine tests explicitly skip when the binary is missing; deterministic
OCR routing/quality unit tests still run. Fixture generation needs the `pdf-dev`
extra already included in `pdf.lock`.

The API extra is activated for P6.1–P6.3 through `api.lock`. The embeddings extra is now activated for
P3.1–P3.3 through the platform-specific lock described below. Acquisition and
settings validation still do not require model dependencies.


`chunking.lock` activates the small Rust-backed `tokenizers` dependency (verified
version 0.23.3) for P2C.1–P2C.3, constrained by the core lock. Its Hub client
transitive dependencies are installed, but chunking does not implicitly contact
the Hub or load embedding weights.

```sh
python -m pip install --require-hashes -r requirements/chunking.lock
python -m piptools compile pyproject.toml --extra chunking --constraint requirements/core.lock --generate-hashes --strip-extras --output-file requirements/chunking.lock --no-emit-index-url --no-emit-trusted-host
dietary-rag prepare-tokenizer
```

The explicit preparation command caches six files at the immutable E5 revision
and verifies their SHA-256 hashes against `config/tokenizer-e5-base-v2.json`.
Tokenizer files have their own pinned manifest and are not Python packages.
Real-tokenizer integration checks skip explicitly if the cache is missing;
unit boundary checks use a clearly labeled deterministic test counter. Install
and prepare this group to run the complete Phase 2 verification offline.
Sentence Transformers, Chroma and model weights are activated separately by
the Phase 3 commands; E5 tokenizer preparation remains independent.

## Embedding and vector-index dependencies

`embeddings.lock` pins Sentence Transformers, Transformers, CPU PyTorch and
Chroma plus their transitive dependencies using the SHA-256 of each selected
wheel. This lock is **specific to macOS ARM64/Python 3.13**. Shared packages are
constrained by all existing locks. Other environments require separate lock
resolution and verification before claiming support.

```sh
python -m pip install --require-hashes -r requirements/embeddings.lock
python scripts/lock_embeddings.py
dietary-rag prepare-embeddings
```

The lock script explicitly resolves binary wheels through pip's dry-run report;
it avoids downloading every platform's large model-runtime wheels just to hash
them. The lock contains versions and hashes, without environment-specific index
URLs or credentials. An explicit `--resolution-report` can regenerate it offline
from a previously saved pip resolution report. Review and test regenerated locks.

BGE model artifacts are separately pinned by
`config/embedding-bge-small-en-v1.5.json`; they are not Python dependencies.
See [Phase 3 indexing](../docs/phase3-indexing.md) for model/cache contracts,
candidate build and compatibility validation commands.

## API and Groq dependencies

`api.lock` pins FastAPI, Uvicorn, HTTPX and python-dotenv for P6.1–P6.3. Groq uses
its OpenAI-compatible HTTPS endpoint directly; no additional model or SDK is downloaded.
Imports remain lazy, so core-only policy/settings/CLI checks do not require API packages.
The lock shares existing core/acquisition/embedding dependency constraints.

```sh
python -m pip install --require-hashes -r requirements/api.lock
PIP_TOOLS_CACHE_DIR=/tmp/dietary-pip-tools python -m piptools compile pyproject.toml --extra api --constraint requirements/core.lock --constraint requirements/acquisition.lock --constraint requirements/embeddings.lock --generate-hashes --strip-extras --output-file requirements/api.lock --no-emit-index-url --no-emit-trusted-host
```

See [API integration](../docs/phase6-api.md) for `.env`, server startup, quotas and tests.

## Streamlit runtime

`linux-streamlit.lock` resolves the Community Cloud CPython 3.13 x86_64 runtime
with CPU Torch and hashes. Install via the root `requirements.txt`, which supplies
a direct hash-verified official CPU wheel URL. `macos-streamlit.lock` is a separate local lock
resolved on the development Mac. Runtime-only packages exclude source acquisition,
PDF parsing and OCR. Inputs and reviewed version constraints are retained in
`streamlit.in` and `streamlit-constraints.txt`. See the
[deployment guide](../docs/streamlit-deployment.md) for regeneration and host checks.
These locks do not extend the old macOS release approval to a Linux deployment.
