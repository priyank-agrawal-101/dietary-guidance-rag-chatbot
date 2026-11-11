# Phase 0 dependency locks

`core.lock` pins and hashes the configuration/contract dependencies. `dev.lock` adds tests,
linting, build and lock-generation tools, constrained by the core lock. They were resolved
with Python 3.13; the project supports Python 3.11 or later. Verify installation on another
Python/platform before claiming compatibility beyond the tested environment.

Regenerate intentionally from `pyproject.toml` in the development environment:

```sh
python -m piptools compile pyproject.toml --generate-hashes --strip-extras --output-file requirements/core.lock --no-emit-index-url --no-emit-trusted-host
python -m piptools compile pyproject.toml --extra dev --constraint requirements/core.lock --generate-hashes --allow-unsafe --strip-extras --output-file requirements/dev.lock --no-emit-index-url --no-emit-trusted-host
```

Review dependency changes, install the locks with `--require-hashes`, and rerun checks.
Runtime dependencies for ingestion, API, embeddings and OCR are optional extras with
bounded version ranges. They are **not yet locked or required by Phase 0**; resolve and
lock each activated group when its implementation begins. Do not install large model
dependencies just to validate settings or source metadata.
