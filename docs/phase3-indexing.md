# Phase 3 foundations: BGE-small and candidate indexing

P3.1–P3.3 implement explicit model preparation, offline BGE-small embeddings,
paired SQLite/Chroma candidate snapshots and compatibility validation. They do
not themselves implement corpus/named-document search, retrieval evaluation or
activation; [P3.4–P3.6 retrieval](phase3-retrieval.md) and
[P3.7 lifecycle](phase3-lifecycle.md) now implement those steps. No chatbot
answering is introduced.

An embedding represents a passage or question as 384 numbers. Related meanings
are expected to have nearby vectors, but retrieval tests must establish whether
the right guidance actually ranks well. SQLite preserves the checkable original
words and locations; Chroma stores the vectors used to find candidate evidence.

## Model and input contract

The selected model is [BAAI/bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5),
pinned to `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`. Its MIT-licensed weights
run locally on CPU without API fees. The model produces 384-dimensional vectors
using CLS pooling followed by normalization. Chroma uses cosine distance.

[The model manifest](../config/embedding-bge-small-en-v1.5.json) records exact
SHA-256 hashes and byte sizes for the 133,466,304-byte Safetensors weights,
tokenizer, model configuration, Sentence Transformers modules and pooling
metadata. Only known Transformer, Pooling and Normalize modules are accepted;
unverified auxiliary cache files are rejected. No pickle weights or remote code
are loaded. Preparation is an explicit network operation. Model loading and
inference use verified local files, with `local_files_only=True`.

Phase 2 chunk records remain immutable. Their stored `embedding_text` and
token counts describe historical E5 preprocessing. BGE passage inputs remove
only the leading `passage: `, retaining the exact document, compact section,
audience and original evidence body. BGE queries use the instruction
`Represent this sentence for searching relevant passages: `, including its
trailing space. Call `embed_query` with the question alone.

The BGE tokenizer, model position limit and Sentence Transformers configuration
agree on **512 tokens**. Complete inputs are checked before inference, including
special tokens and query instructions. The adapter also checks the model's
actual tokenization against the independent untruncated count. Over-limit
passages require a reviewed rebuild; questions are rejected rather than
shortened. BGE-specific input text, hash and token count are stored separately
from canonical chunks. Invalid dimensions, nonfinite values or unnormalized
vectors are rejected.

## Candidate snapshot and compatibility checks

`build-index` reads one explicit consumer staging snapshot from the existing
chunk database. It verifies snapshot identity and membership, current registry
eligibility, unique families, review receipts, exclusions and native evidence,
then reproduces each build with its historical E5 tokenizer/configuration.
Comparison sources, synthetic fixtures and quarantined normalization cannot be
admitted to the consumer candidate.

The builder creates a fresh temporary directory containing:

- `evidence.sqlite`: only the selected snapshot's canonical chunks, normalized
  blocks, native exports and reviewed build records, plus BGE input/vector rows
  and a separate index schema/configuration record.
- `chroma/`: automatically persisted vectors with scalar document/version,
  corpus, embedding configuration, family, audience, role, source tier, rights
  status, eligibility and chunk-type metadata. Chroma has no implicit embedding
  function; vectors are always supplied by the verified adapter.
- `manifest.json`: content-derived corpus ID, timezone-aware build time,
  original chunk snapshot, full embedding configuration and dependency versions,
  exact chunk/input membership and canonical vector hash.
- `validation.json`: compatibility evidence, with `active: false` and
  `retrieval_quality_evaluated: false`.

Compatibility validation checks SQLite integrity/foreign keys, exact chunk and
vector membership, evidence replay, model/preprocessing identity, token counts,
input hashes, normalized vectors, canonical vector hashes, cosine configuration
and every vector's scalar metadata. Matching dimensions alone do not establish
model compatibility. Corrupt or incomplete candidates fail validation.

Only after validation does the temporary paired directory move to its
content-derived final name. Failed builds leave no published candidate. Repeated
unchanged inputs/configuration reuse the same validated candidate. Build time
is audit metadata rather than part of its deterministic identity. The source
chunk database and active-version pointer are never changed. Model, dependency,
preprocessing or evidence changes produce a different candidate identity.

Chroma persists automatically. The pinned runtime closes its clients before
moving or reopening a paired directory; the compatibility helper also supports
older Chroma 1.x releases without a public close method. Concurrent builds to
the same destination are not coordinated by a distributed lock; this remains
a separately invoked, single-administrator local ingestion workflow.

## Install and run

The embedding dependency lock records selected wheel hashes for **macOS ARM64,
Python 3.13**. Other platforms/Python versions need a separately resolved and
verified lock; model inference remains CPU-based. Install alongside the existing
core, extraction and chunking locks:

```sh
python -m pip install --require-hashes -r requirements/embeddings.lock
dietary-rag prepare-embeddings
```

Runtime `embedding` settings now select BGE-small. The separate
`chunking.tokenizer_model_name` / `chunking.tokenizer_revision` settings retain
E5 for reproducing historical chunks. `prepare-tokenizer`, `chunk-sources` and
the existing chunk comparisons continue using that E5 manifest.

Select the exact `snapshot.snapshot_id` from the reviewed 350-token chunk report:

```sh
dietary-rag build-index \
  --chunk-snapshot chunk_snapshot_e9e8b804e406477b528835fd71163f40bcffa5bf85c3552168300ecef26fe921 \
  --report data/staging/index-build-report.json

dietary-rag validate-index \
  --snapshot-dir <snapshot_dir from the build report> \
  --report data/staging/index-validation-report.json
```

The reviewed development corpus has 269 chunks across 16 documents, including
six official PDFs. Recompute counts from the selected snapshot when evidence
changes. Paths, manifests, model/tokenizer caches and report destinations can
be overridden explicitly. Successful commands return 0; invalid inputs,
incompatible configuration or evidence failures return 2, following the shared
administrative CLI error contract. Imports never load heavy packages or models.

To resolve indexed evidence, open the candidate's `evidence.sqlite` with
`ChunkRepository(..., read_only=True)` and call `resolve_chunk(chunk_id)`.
`BGEEmbedder` exposes `input_record`, `encode` and `embed_query`; the candidate
validator verifies its complete configuration before subsequent retrieval work.

To display a few vectors already stored in Chroma, with their canonical chunk
text and source details, run from the activated project environment:

```sh
python scripts/inspect_embeddings.py
python scripts/inspect_embeddings.py --document-id mn_safe_grilling_pdf --limit 2 --full-vector
python scripts/inspect_embeddings.py --limit 3 --json
```

The default reads the snapshot selected by `data/staging/index-build-report.json`
and previews 12 values for each of three stored vectors. Use `--snapshot-dir`
or `--build-report` to select another snapshot, `--dimensions` for a longer
preview, or `--json` for complete text, metadata and vector values. Inspection
uses existing stored embeddings; it does not load model weights, generate
embeddings, modify evidence or activate the index.

Tests cover preprocessing fidelity, limit rejection before inference, download
failure cleanup, corrupted caches, vector validity, source preservation,
deterministic candidate reuse, CLI builds/checks, corrupted evidence/indexes
and offline real-model inference. Chroma tests explicitly skip without the
embedding dependencies; real-model tests additionally require the prepared
cache and never download models themselves.

The verified workspace ran **371 tests with no skips**, including 35 new
embedding/index checks, real pinned-model inference and Chroma persistence.
The complete 269-chunk candidate also passed independent validation with Hub
and Transformers offline modes enabled. Its BGE complete inputs measure
**18 / 59 / 335 tokens** (minimum / median / maximum). Lint, formatting,
dependency consistency, registry validation and wheel build passed. See the
[review report](embedding-index-review.json) for snapshot identities, exact
model/dependency configuration, source counts and verification evidence.

P3.4–P3.6 now implement scoped dense/BM25/hybrid retrieval and frozen strategy
evaluation. Hybrid passed 16/16 regressions and 32/32 held-out hits@5; see
[retrieval commands and measured review](phase3-retrieval.md). P3.7 activation
and rollback are implemented in the [lifecycle guide](phase3-lifecycle.md).
Compatibility success proves that vectors and
evidence belong together; retrieval quality is established separately on the
small authored evidence diagnostic and does not establish answer accuracy.
