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

Remaining API and embeddings extras have bounded version ranges.
They are **not yet locked or required for acquisition**; resolve and
lock each activated group when its implementation begins. Do not install large model
dependencies just to validate settings or source metadata.


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
Sentence Transformers, Chroma and model weights remain unactivated Phase 3 work.
