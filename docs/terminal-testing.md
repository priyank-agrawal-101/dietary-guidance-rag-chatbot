# Testing search from the terminal

This guide explains how to try the active hybrid search, what its output means,
and how we verified it. These commands retrieve stored evidence passages;
they do not yet generate a chatbot answer.

The recorded checks below are from **November 12, 2026**, using 269 reviewed
chunks from 16 documents, including six official PDFs. Later releases may
have different document counts, release IDs and rankings.

## 1. Prepare the terminal

Open a terminal in this project folder and activate the existing environment:

```sh
source .venv/bin/activate
```

The environment, prepared local model and index must already exist. For setup
or rebuilding, see [indexing](phase3-indexing.md) and
[activation](phase3-lifecycle.md).

## 2. Check what is active

```sh
dietary-rag corpus-status
```

Look for these fields:

| Field | Meaning |
| --- | --- |
| `active: true` | A snapshot and retrieval configuration have been published together. |
| `state.generation` | The publication counter. Activation or rollback increments it; repeating the identical release does not. |
| `state.active_release_id` | The release currently selected for new requests. |
| `state.previous_release_id` | The previous release available for rollback, or `null` after the first activation. |
| `active_release.configuration.strategy` | The selected search method; our active release uses `hybrid`. |
| `active_release.validation` | The compatibility checks recorded when this release was validated. |

Our workspace activation was generation **1** with no previous release. Its
validation recorded **269 chunks, 16 documents and six PDFs**. Status inspection
checks retained records; a search also validates snapshot compatibility before
querying.

The source of current activation is `data/active/active.json`. An older
`manifest.json`, `validation.json` or build report can still say `active: false`:
those are immutable records from candidate creation, not the current pointer.

## 3. Search all documents

```sh
dietary-rag search-corpus \
  "What special requests reduce restaurant sodium?" \
  --active --report data/staging/manual-full-search.json
```

`--active` uses the validated winning configuration. Hybrid combines BGE-small
meaning-based retrieval with BM25 keyword retrieval, then merges their ranks.
The default output contains at most five hits. Add `--top-k 20` to inspect more
candidates.

In our recorded run, the top result came from **`nhlbi_sodium_tips_pdf`**. The
top five included the complete “Make special requests” passage, with its
without-added-salt and MSG qualifications. The audit confirmed that all **16
documents** were searched; the top results need not represent all 16.

## 4. Restrict the search to one document

```sh
dietary-rag search-corpus \
  "How long can hard cooked eggs keep?" \
  --active --documents mn_storage_chart \
  --report data/staging/manual-named-search.json
```

In the recorded run, the first hit was an eggs table chunk containing the
hard-cooked row: **7 days refrigerated at 40°F**, and **don’t freeze**. Read the
correct row and its notes: that chunk also contains other egg products with
different values.

Check that `audit.searched_document_ids` contains only `mn_storage_chart` and
every returned `document.document_id` matches it. A document restriction must
never silently become a full-corpus search.

You can also use a known name or alias:

```sh
dietary-rag search-corpus \
  "According to the Minnesota storage chart, how long can hard cooked eggs keep?" \
  --active
```

## 5. Read the search JSON

The main sections are `retrieval_configuration`, `hits` and `audit`.

| Output field | What to read it as |
| --- | --- |
| `active` | Whether this command used the active release. Candidate inspection returns `false`. |
| `retrieval_configuration.strategy` | `hybrid`, `dense` or `bm25`. |
| `hits` | The returned evidence chunks, in final rank order. |
| `hits[i].rank` | Final position: rank 1 is the first result. |
| `hits[i].chunk.text` | The actual canonical evidence to read. |
| `hits[i].document` | Source name, publisher, URL and document metadata. |
| `hits[i].chunk.chunk_id` | The exact chunk identity used for tracing evidence. |
| `hits[i].chunk.chunk_type` | `prose`, `list` or `table`. |
| `hits[i].chunk.source_locations` | Where the passage came from: PDF pages/coordinates or HTML locations. |
| `hits[i].branch_ranks` | Its rank in each contributing search branch. A missing branch means it did not contribute this chunk. |
| `hits[i].score` | The selected strategy’s ranking score, not confidence that an answer is correct. |

For example, our first sodium result had these real values, with other fields
omitted here for readability:

```json
{
  "rank": 1,
  "score": 0.031746031746031744,
  "branch_ranks": {"dense": 3, "bm25": 3}
}
```

Hybrid adds `1 / (60 + rank)` from each branch. Here that is
`1/63 + 1/63`, or approximately `0.031746`. It **does not mean 3.17% confidence**.
Final ranks can differ from branch or fused ranks because evidence-preserving
deduplication and candidate limits also apply.

For table hits, inspect `table_rows`, `table_context` and the labeled text
together. `[blank in source]` means the original source left a cell empty;
it does not mean zero or permission to infer a missing value.

`chunk.embedding_text` and `chunk.token_count` retain the historical E5 chunk
preprocessing records. BGE passage inputs remove only the leading `passage: `;
BGE-specific input hashes/counts are stored separately. Read `chunk.text` as
the evidence body.

### Understand the audit

| Audit field | Meaning |
| --- | --- |
| `status` | `searched`, `invalid_selection` or `no_eligible_documents`. |
| `selected_document_ids` | The explicit restriction, or `null` for ordinary full-corpus search. |
| `searched_document_ids` | Documents actually included in the search, not just those producing top hits. |
| `release_id` and `activation_generation` | The active release pinned for this request. |
| `corpus_snapshot_id` and `retrieval_configuration_id` | The exact data and search settings used together. |
| `branch_ranks` | Ranked candidates from dense and/or BM25 retrieval. |
| `fused_ranks` | The combined ranking in hybrid mode, before final result selection. |
| `scope_issues` | Unknown, ambiguous, unavailable or conflicting selections. |
| `filtered_documents` | Documents excluded by scope or eligibility filters, with reasons. |
| `budget_omissions` | Evidence left out because of candidate/contribution limits. |
| `duplicate_omissions` | Lower-ranked slices omitted because another retained chunk fully covers them. |
| `query_embedding_seconds` and `elapsed_seconds` | Query embedding time and retrieval time, in seconds. |

An empty `hits` array alone does not explain what happened. Read `audit.status`
and any issues. Operational failures such as a missing model or incompatible
snapshot produce an error rather than a successful “not covered” search.

### Print just the readable passages

After saving a report with `--report`, this prints its scope and evidence with
normal Unicode characters rather than JSON escapes such as `\u00b0F`:

```sh
python - <<'PY'
import json
from pathlib import Path

report = json.loads(Path("data/staging/manual-full-search.json").read_text())
print("Strategy:", report["retrieval_configuration"]["strategy"])
print("Status:", report["audit"]["status"])
print("Searched:", ", ".join(report["audit"]["searched_document_ids"]))
for hit in report["hits"]:
    print(f"\nRank {hit['rank']} — {hit['document']['document_name']}")
    print(hit["chunk"]["text"])
    print("Source:", hit["document"]["source_url"])
PY
```

Model-loading messages may appear in the terminal. The file written by
`--report` contains the structured JSON result.

## 6. Try a selection failure

```sh
dietary-rag search-corpus "How should food be stored?" \
  --active --documents unknown_document
```

Expect `audit.status: "invalid_selection"`, an `unknown` scope issue, no hits
and no searched documents. The command exits with code **1**. Immediately
after a command, `echo $?` displays its exit code:

| Exit code | Meaning |
| --- | --- |
| `0` | The command completed successfully. Search relevance still needs inspection. |
| `1` | Search had an invalid selection or no eligible documents; evaluation uses this code when no strategy passes. |
| `2` | Invalid arguments/settings, missing artifacts or another operational failure. |

For example, `--active --strategy bm25` is rejected with code 2 because an
active request cannot override the pinned retrieval configuration.

## 7. Inspect stored embeddings

The [inspection script](../scripts/inspect_embeddings.py) shows existing
vectors alongside their chunks. It does not perform query retrieval or
generate new embeddings.

```sh
python scripts/inspect_embeddings.py --limit 3
python scripts/inspect_embeddings.py --document-id nhlbi_sodium_tips_pdf --limit 1
python scripts/inspect_embeddings.py --limit 1 --full-vector
```

| Output | Meaning |
| --- | --- |
| `Snapshot`, `Model`, `Collection` | The stored index being inspected. |
| `269 stored vectors` | One stored embedding for each chunk in the reviewed baseline. |
| Chunk/document/type/source | The evidence represented by the displayed vector. |
| `384 dimensions` | BGE-small represents the passage with 384 numbers. |
| `L2 norm` close to `1.000000` | The stored vector is normalized to unit length. |
| `Values (12/384)` | A preview of the first 12 numbers; use `--full-vector` to see all values. |

Individual vector numbers are not facts, probabilities or readable word
labels. Their relationships help meaning-based retrieval find similar passages.
Samples are not relevance-ranked results because no question was supplied.

This script defaults to the **index build report**, not the active pointer.
They select the same snapshot in the recorded workspace. If they later differ,
use `--snapshot-dir` with the active release’s `snapshot_directory` to inspect
that specific index. `--json` returns complete text, metadata and vectors.

## 8. How we tested and selected hybrid

We checked all three strategies on the same reviewed snapshot and frozen
questions. There were 16 existing regression questions and 32 separately
authored held-out questions covering eight guidance categories. Expected
document IDs were evidence labels, not automatic search restrictions.

| Strategy | Regression complete-evidence hits@5 | Held-out hits@5 | Outcome |
| --- | ---: | ---: | --- |
| BM25 keywords | 16/16 | 31/32 | Passed, but fewer held-out hits. |
| Dense BGE-small | 15/16 | 32/32 | Failed the regression gate. |
| Hybrid | 16/16 | 32/32 | Passed and selected. |

A complete-evidence hit@5 means at least one of the first five chunks contains
all expected facts and qualifications from an acceptable source. A related
passage or the wrong table row is a miss. Dense missed the complete restaurant
sodium passage; BM25 missed the complete finned-fish safety evidence. Hybrid
covered both. Each strategy passed 104 scope checks with complete provenance
resolution.

We then verified activation, concurrent publishers, failed-publication
recovery, retained proof integrity and rollback. In-flight requests kept their
original configuration and later evidence during release switches. A real
rollback demonstration used an isolated store, leaving the workspace’s first
active release intact. The final recorded suite passed **435 tests with no
skips**, plus lint, dependency consistency and wheel build checks.

To rerun the existing diagnostics without activating a new release:

```sh
python -m pytest
dietary-rag evaluate-retrieval --regressions-only
dietary-rag evaluate-retrieval --output-dir data/staging/manual-retrieval-evaluation
```

Evaluation writes comparison reports and a selected candidate configuration;
it does not change the active pointer. Frozen held-out questions must not be
used for tuning. These are small project-authored evidence checks, not proof
of generated-answer accuracy or performance on all possible questions.

The recorded search reports are
`data/staging/active-search-verification.json` and
`data/staging/active-named-search-verification.json`. Full evaluation output is
under `data/staging/retrieval/p3-review/`; downloaded/prepared data and staging
reports are ignored by Git. The checked-in summaries are
[retrieval review](retrieval-review.json) and
[lifecycle review](lifecycle-review.json).
