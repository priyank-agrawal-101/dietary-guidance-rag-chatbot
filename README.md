# dietary-guidance-rag-chatbot

Build a prototype for a US audience using **HTML pages and PDF documents**, prioritizing authoritative government guidance. Every factual claim must come from retrieved evidence and carry a citation, including page references for PDFs. The service reports evidence gaps and declines medical advice and calorie/weight targets.

The [source registry](config/sources.yaml) records 61 inspected candidate URLs and retains all 20 earlier IDs. The [source review](docs/sourceReview.md) documents access, audience, currency, rights, duplicate formats and 10 verified official PDFs; 7 PDFs are approved with exclusions. The proposed default selection has 10 distinct consumer guidance families before extraction/indexing, so additional eligible coverage is still needed for the initial 15–20-family target.

See the [problem statement](docs/problemStatement.md) and [architecture](docs/architecture.md) for HTML/PDF extraction, OCR fallback, cleaning, normalization, chunking, indexing and citation requirements. Phase 0 provides an installable Python package, validated settings, provenance contracts and identity helpers. The chatbot and ingestion runtime are not implemented yet. Downloaded source snapshots and inspection renders are ignored by Git.

Follow the [phase-wise implementation plan](docs/implementation-plan.md) for build dependencies, tasks, deliverables, learning outcomes and completion gates.

Use Python 3.11 or later; this machine has `python3.13`, while its default `python3` is 3.9. From the repository root:

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements/dev.lock
python -m pip install --no-deps --no-build-isolation -e .
dietary-rag check-settings
python -m pytest
python -m ruff check src tests
python -m ruff format --check src tests
```

See [Phase 0 foundations](docs/phase0-foundations.md) for configuration precedence, synthetic contract examples, identity/version rules, dependency groups and wheel installation. Core dependencies are hash-locked; ingestion, API, model and OCR extras are reserved for later phases. Credentials are not required for Phase 0.

Validate the registry in the installed environment:

```sh
python scripts/validate_source_registry.py
# Optional later check: requires the local review cache and the ingestion dependency group.
python scripts/validate_source_registry.py --check-cache
```
