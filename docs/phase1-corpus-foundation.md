# Phase 1 Corpus Foundation: P1.1 and P1.2

P1.1 implements registry loading and consumer acquisition eligibility. P1.2 implements
content-addressed original-byte storage and SQLite provenance repositories. These are
foundations for the next [implementation-plan](implementation-plan.md) tasks. P1.3–P1.6
now implement saved-file import, response inspection, HTTP fetching and acquisition CLI
operations; see [source acquisition](phase1-acquisition.md). The P1.1/P1.2 work described
here did not itself import review-cache files; the subsequent import task has done so.

## Registry loading and eligibility

```sh
source .venv/bin/activate
dietary-rag check-registry
dietary-rag check-registry --include-decisions
```

The command is read-only. It uses the same settings/project-root/environment behavior as
`check-settings`, loads `config/sources.yaml`, and reports reviewed ingestion candidates.
It does not read source snapshots, create databases/directories, fetch content or activate
a corpus. The original P1.1/P1.2 baseline retained 61 entries and all 20 existing IDs, with
10 consumer candidates (6 PDFs and 4 HTML assets). P1.7 now retains 68 entries and selects
16 eligible families (6 PDFs and 10 HTML assets); see [coverage review](phase1-coverage.md).

`load_registry(path)` returns typed source, policy, access, selection, date and PDF
inspection models. Duplicate YAML keys/IDs, missing preserved IDs, stale count summaries,
invalid PDF review metadata and contradictory alternate-family relationships are rejected.
Two proposed defaults in one family cause an explicit error; the loader never arbitrarily
chooses one or silently substitutes an alternate edition.

`registry.get(id)` preserves access to every reviewed entry. Unknown IDs raise `KeyError`.
`consumer_sources()` returns only sources passing all consumer acquisition filters, and
`consumer_eligibility(id)` returns reason codes and outstanding activation gates.

The filters require an approved/default proposal, eligible sections, US consumer role
and applicability, verified access/format/acquisition metadata and an accepted recorded
local-text rights status. Unknown or unresolved rights statuses fail closed. The policy
cannot broaden default retrieval to medical, scientific, historical, regulatory or
international content. Rights acceptance here is for the registry's attributed local
educational text milestone, not blanket redistribution permission.

The three accepted recorded rights statuses are `permitted_government_text_with_exclusions`,
`explicit_document_reuse_notice` and `text_reviewed_no_explicit_reuse_barrier_observed`.
Adding a new rights status requires an intentional implementation/review change; spelling
a new status in YAML does not automatically authorize ingestion.

These sources are **candidates**, not ready indexed documents. Source exclusions, table
fidelity, actual byte-format checks, date/applicability review and downstream quality gates
still apply. The loader relies on recorded review evidence; P1.4 validates new responses.
It does not resolve named-document aliases or automatically select inactive alternates;
that behavior belongs to the later resolver after edition comparison.

## Immutable original-byte artifacts

`ArtifactStore(root)` is explicit and creates no directories until `put_bytes` is called.
Original bytes are stored without cleaning, newline conversion or HTML/PDF parsing:

```python
from pathlib import Path
from dietary_rag.corpus.artifacts import ArtifactStore

store = ArtifactStore(Path("data/staging/raw"))
artifact = store.put_bytes(b"Project-created example bytes\r\n")
assert store.read_bytes(artifact.sha256) == b"Project-created example bytes\r\n"
```

Artifacts use `sha256/<prefix>/<digest>.blob` relative to the configured store root.
Format is provenance metadata, not inferred from `.blob`. SHA-256 is recomputed on reads.
Writes use a flushed same-directory temporary file and exclusive hard-link publication;
duplicate writers reuse one complete artifact without overwriting it. Temporary files are
removed, and unchanged artifacts are reused. Empty input, malformed digests, path escapes,
symlink artifacts and externally corrupted bytes are rejected.

Immutability is enforced by this API, not a guarantee against an owner editing files or
the database directly. Corruption is surfaced rather than repaired by overwriting evidence.

## SQLite provenance repository

Opening a repository is explicit. Imports and constructors do not initialize a database.
Use a context manager to create/open the database and close its connection reliably:

```python
from pathlib import Path
from dietary_rag.corpus.artifacts import ArtifactStore
from dietary_rag.corpus.registry import load_registry
from dietary_rag.corpus.repository import CorpusRepository

registry = load_registry(Path("config/sources.yaml"))
store = ArtifactStore(Path("data/staging/raw"))
with CorpusRepository(Path("data/staging/provenance.sqlite"), store) as repository:
    snapshot_id = repository.save_registry(registry)
    assert repository.get_registry(snapshot_id) == registry
    assert repository.get_source(snapshot_id, "foodsafety_cold_storage").selection.decision == "pending_access"
```

This example stores review metadata only, including restricted/unavailable records. It
does not ingest their article text or create vectors. Database paths and the artifact root
are caller-selected; keep both under the configured data directory. `AcquisitionRecord.raw_path`
must be the artifact's **store-relative path**, not an arbitrary source-cache path.

| Table | Purpose |
| --- | --- |
| `documents` | Stable IDs across source/review/content changes. |
| `registry_snapshots` | Immutable whole-registry policy/review snapshots with deterministic identities. |
| `registry_sources` | Source metadata and family/date/rights/selection records within each snapshot. |
| `artifacts` | SHA-256, byte count and store-relative path of verified stored bytes. |
| `acquisitions` | Typed success/failure observations linked to known IDs and optional stored artifacts. |
| `document_versions` | Version metadata linked to a successful acquisition, reviewed source and raw artifact. |

`save_acquisition(record)` verifies any referenced artifact's bytes, size and relative
path before committing. Failed acquisitions may retain status/error/observed byte counts
without a stored response body. They do not become document versions.

`save_document_version(document, acquisition_id=..., registry_snapshot_id=...)` checks
source identity, reviewed family/format/attribution/dates/rights/selection, acquisition
success, original/resolved URLs where supplied, timestamp and raw hash. A known reviewed
source hash and PDF inspection must match. Changed bytes or review metadata require a
new review snapshot and version; an old version ID cannot be overwritten.

Getters reconstruct Phase 0 models without inventing dates or losing observations,
including NIDDK's structured CMS date fields. Writes revalidate contracts, use bound SQL
parameters, enforce foreign keys and commit/rollback as a unit. Repeat writes are
idempotent. Schema version 1 is initialized for a new empty database; unknown versions
or unversioned existing databases are rejected rather than migrated automatically.

Use one repository context/connection per caller; SQLite's normal thread restrictions
apply. A database transaction does not encompass filesystem publication: an interrupted
later import may leave an unreferenced artifact, never a partial published artifact.
P1.3–P1.6 provide explicit import orchestration and idempotent retries; automatic cleanup
of unreferenced artifacts is not implemented.

## Verification and remaining work

Tests use isolated temporary stores/databases and synthetic bytes. They verify eligibility,
family conflicts, metadata snapshots, exact binary/HTML byte preservation, PDF edition/page
metadata round trips, repeated/concurrent artifact writes, corruption detection, foreign
keys, failed acquisitions, provenance mismatches and version conflicts. They do not claim
to validate PDF parsing or extraction from these synthetic storage fixtures.

```sh
python -m pytest
python -m ruff check src tests
python -m ruff format --check src tests
python scripts/validate_source_registry.py
```

P1.3's reviewed-file import, P1.4's actual response/file validation, P1.5's bounded fetching
and P1.6's acquisition commands are complete; see [source acquisition](phase1-acquisition.md).
P1.7's coverage expansion is complete: 16 eligible consumer families and six default PDF
acquisitions are byte-verified. The release target still requires reviewed searchable
chunks. Blocks/chunks, active corpus
snapshots, vectors and generated answers are later phases.

### Completed verification

P1.1/P1.2: 87 tests passed on Python 3.13, including the original 44 Phase 0 tests.
Ruff lint/format checks and the wheel build passed. The CLI and original source validator
agree on 61 retained sources and 10 unique consumer candidates. Tests verify HTML and
PDF metadata round trips and failed-acquisition rejection in temporary SQLite stores;
no external downloads or migration of the review cache were required.
