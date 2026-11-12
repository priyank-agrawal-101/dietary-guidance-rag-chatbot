# Phase 3 activation, request pinning and rollback

P3.7 implements local atomic publication of the **validated corpus snapshot and
passing retrieval configuration together**. The reviewed hybrid configuration
passes 16/16 regressions, 32/32 held-out complete-evidence hits@5, scope checks
and provenance resolution over 269 chunks from 16 sources, including six
official PDFs. See [retrieval selection](phase3-retrieval.md) and the
[lifecycle verification record](lifecycle-review.json).

## Activate and inspect

For a beginner walkthrough, saved report examples and explanations of each
output field, see [terminal testing](terminal-testing.md).

```sh
dietary-rag activate-corpus \
  --selection-report data/staging/retrieval/p3-review/selection.json \
  --retrieval-config config/retrieval-hybrid.json \
  --expected-generation 0

dietary-rag corpus-status
dietary-rag search-corpus "What special requests reduce restaurant sodium?" --active
dietary-rag search-corpus "How long can hard cooked eggs keep?" \
  --active --documents mn_storage_chart
```

The workspace's reviewed release has been activated. The activation example
uses generation 0 for an initially empty store; a repeated operation should
use the generation reported by `corpus-status` or omit the optional condition.
Repeating the identical release is idempotent and preserves its rollback
target. `--snapshot-dir` can specify the candidate explicitly; otherwise the
build report supplies it. `--lifecycle-dir` selects an isolated store for
testing. Default state lives under `data/active`, respecting the configured
data directory.

`--active` reads the published pair; it rejects `--strategy`,
`--retrieval-config` and `--snapshot-dir` overrides. Candidate inspection with
an explicit strategy/configuration remains available independently. Missing
or corrupt active state is an operational failure, with no candidate or
previous-release fallback search. Active search audits include the release ID,
activation generation, corpus ID and retrieval-configuration ID.

## Publication gate

Activation revalidates the complete SQLite/Chroma candidate using the current
reviewed registry, historical chunk tokenizer and pinned BGE configuration.
It requires reviewed HTML and at least five approved official PDFs. It
reconstructs the three-strategy selection, verifies the report identity,
configuration identity and winning strategy, then checks the retained frozen
questions and expected evidence against canonical chunks.

All 48 question results must use their exact full-corpus questions and scopes,
valid ranks within five results and canonical document/type identities.
Complete-hit claims are recomputed from the actual chunk facts and required
qualifications; each hit resolves through SQLite provenance. The gate verifies
the full set of 104 recorded scope checks, matching dependency configurations
and the same comparison machine. A passing but unselected strategy cannot
replace the winner. Failed validation leaves the active pointer unchanged.
These checks revalidate the stored frozen comparison; they do not retune or
rerun held-out ranking, and do not establish generated answer quality.

Each immutable release directory retains its descriptor, complete selection
proof and exact regression, held-out and freeze files. The descriptor pins
the snapshot's absolute path and manifest hash, embedding-configuration ID,
retrieval configuration and artifact hashes. Changes to external comparison
files cannot change an already retained rollback proof.

## Atomic state and retention

`corpus/lifecycle.py` serializes publishers with a Unix advisory file lock.
Release files are flushed and fsynced before publication. A temporary pointer
containing both release references is fsynced and atomically replaced, followed
by a directory fsync. Concurrent publishers cannot mix snapshot/configuration
versions; optional `--expected-generation` prevents stale administrative
operations. Readers observe the old or new complete pointer without waiting
for validation. Interrupted publication may leave an unreferenced immutable
release, but never a partially written pointer.

The store layout is:

```text
data/active/
  active.json                 # generation, active and previous release IDs
  publication.lock
  releases/<release_id>/
    release.json              # pinned pair, validation and artifact hashes
    selection.json
    regressions.json
    heldout.json
    freeze.json
```

Snapshots remain at their original immutable index directories. Lifecycle
operations never rebuild vectors, modify canonical evidence or delete old
snapshots/releases. Retain those directories and the prepared model/tokenizer
artifacts for in-flight requests and rollback. Retention/garbage collection is
not implemented. Local publication currently supports Unix/macOS/Linux file
locking and one filesystem for the lifecycle store.

The candidate `manifest.json`, `validation.json`, build report and checked-in
retrieval artifact retain their historical `active: false` values. **Current
activation is determined by `data/active/active.json`**, rather than rewriting
those immutable artifacts. `corpus-status` reports the current pair and its
previous release; a damaged previous record is reported separately without
mislabeling a healthy active release.

## Request pinning and evidence

```python
from dietary_rag.corpus.lifecycle import LifecycleStore
from dietary_rag.retrieval.contracts import SearchRequest

pin = LifecycleStore(settings.data_dir / "active").pin()
with pin.open_request(registry, tokenizer, processor, resolver, settings.runtime) as request:
    result = request.search(SearchRequest(question="How should food be stored?"))
    if result.hits:
        evidence = request.resolve_evidence(result.hits[0].chunk.chunk_id)
```

`pin()` reads the atomic pointer exactly once. Opening the request checks the
retained manifest/configuration identities and validates that snapshot before
querying. Its retriever, SQLite handle and returned evidence remain on that
pair even when activation or rollback changes the pointer. Later evidence
resolution uses the same open repository and admits only chunks actually
retrieved by that request. This provides the evidence boundary for later
citation rendering; generated answers and citation presentation remain
Phase 5 work. Handles close when the request context exits.

## Rollback

```sh
dietary-rag rollback-corpus --expected-generation 2
dietary-rag corpus-status
```

Rollback revalidates the retained previous pair, its stored selection proof
and frozen files under the current configured registry/model/tokenizer. It
atomically swaps active and previous references and increments the generation,
restoring the snapshot and retrieval configuration together. In-flight requests
remain on their original pair. A second rollback can restore the pair just
replaced. With no previous release, or incompatible/corrupt retained evidence,
rollback fails explicitly and preserves the current pointer. A first activation
has no previous release; rollback behavior is demonstrated in an isolated
store and tested across distinct snapshot/configuration pairs.

## Verification

Tests cover atomic failure recovery, idempotence, stale-generation rejection,
concurrent publishers, readers during validation, retained artifact corruption,
missing active/previous state, CLI override rejection and rollback. Real
Chroma fixtures exercise retrieval and evidence resolution during configuration
switches. The real frozen comparison passes the publication gate without
loading query weights; altered questions, scope checks, hit claims and
unselected configurations are rejected.

The local demonstration activates two distinct release proofs for the same
reviewed winning pair in an isolated store, rolls back and performs a pinned
hybrid search. Tests also switch distinct synthetic snapshot/configuration
pairs to verify that both references restore together; those fixtures make no
retrieval-quality claim. The workspace store activates the original reviewed
winning pair. Phase 3 is complete; next implement Phase 4 policy routing before
adding grounded answer generation.
