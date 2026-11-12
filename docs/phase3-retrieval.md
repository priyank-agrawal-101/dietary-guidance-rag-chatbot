# Phase 3 scoped retrieval and measured selection

P3.4–P3.6 are implemented. **Hybrid is the selected candidate strategy** over
the existing 269 reviewed chunks from 16 sources, including six official PDFs.
The BGE-small model, chunk IDs, 350/50 boundaries and paired evidence/vector
snapshot are unchanged. P3.7 now implements [activation, active-request pinning
and rollback](phase3-lifecycle.md); this guide covers candidate inspection and
the original frozen strategy selection.

## Search implementation

`retrieval/engine.py` exposes one `Retriever` and typed `SearchRequest` for
`dense`, `bm25` and `hybrid`. Opening a retriever validates the complete paired
snapshot with its pinned model/tokenizer configuration and reviewed registry.
Requests resolve their document scope before ranking. US applicability,
consumer eligibility/role, reviewed rights, source tier, canonical family,
document/version and snapshot restrictions apply to every branch. Selected
documents failing a filter invalidate the whole selection.

Dense uses actual local Chroma cosine queries and the pinned BGE query
instruction. BM25 caches scope-local lexical statistics over the stored
document/section/audience context plus body, removing the historical leading
`passage: ` prefix. It verifies model artifacts/tokenization compatibility but
does not instantiate PyTorch or query-embedding weights. Its deterministic
ASCII alphanumeric tokenization uses `k1=1.2` and `b=0.75`; there is no FTS5 or
new persistent lexical index. Hybrid merges ranks by equal-weight
`1 / (60 + rank)`, never by adding the raw branch scores.

Ordinary searches take 20 candidates per branch. Set `--comparison` for broad
multi-source collection, or select multiple document IDs: these searches
collect up to three candidates per document per branch and cap final results
at 20, with omitted evidence recorded separately. Default inspection returns
five hits; `--top-k 20` exposes the bounded candidate set. Conservative
deduplication suppresses only lower-ranked prose/list slices whose text and
original spans are fully covered in the same document, section and region.
It preserves different table rows and additional qualifications.

Every returned hit includes the canonical chunk, original spans/table-row
references, reviewed HTML/PDF source locations, document metadata, rank and
branch ranks. The audit identifies the corpus and retrieval versions, actual
searched document set, filtered documents, branch/fused ranks, budget and
duplicate omissions. Evidence resolves again against the same read-only
SQLite database before returning. An empty search is distinct from invalid
selection and operational failure; similarity is not proof of answer support.

## Commands

Use the prepared artifacts and candidate from [P3.1–P3.3](phase3-indexing.md).
The default candidate location comes from
`data/staging/index-build-report.json`; `--snapshot-dir` selects another
compatible candidate. Searches require an explicit strategy or a compatible
versioned retrieval configuration.

```sh
dietary-rag search-corpus "What special requests reduce restaurant sodium?" \
  --strategy hybrid --report data/staging/retrieval/search.json

dietary-rag search-corpus "How long can hard cooked eggs keep?" \
  --strategy bm25 --documents mn_storage_chart

dietary-rag search-corpus "According to the Minnesota storage chart, how long can eggs keep?" \
  --strategy hybrid

dietary-rag search-corpus "What do these sources say about cooking temperatures?" \
  --strategy hybrid --documents cdc_food_safety mn_safe_grilling_pdf --top-k 20

dietary-rag search-corpus "What do sources say about safe cooking and storage?" \
  --strategy hybrid --comparison --official-only --top-k 20
```

`--document-name` resolves exact normalized registry names, IDs or curated
[aliases](../config/document-aliases.json). `According to NAME, QUESTION` and
`According to NAME: QUESTION` provide the same explicit selection. Unicode,
case, punctuation and a leading “the” are normalized. Ambiguous names such as
“CDC”, unknown names/IDs, known unavailable sources such as Healthy Eating
Plate, malformed selectors and conflicting names/IDs return explicit issues
with no search fallback. `--source-tier` can narrow eligible evidence;
`--official-only` cannot admit excluded sources. Question length, selection
count, query token length, result budget and elapsed request limits are bounded.

## Frozen comparison

```sh
dietary-rag evaluate-retrieval --regressions-only
dietary-rag evaluate-retrieval --output-dir data/staging/retrieval/p3-review
```

The runner evaluates each strategy in a fresh sequential process on the same
machine and snapshot. Regression-only runs cannot select or activate a
strategy. [The 32 held-out questions](../tests/fixtures/retrieval/heldout.json)
were authored separately from the 16 regressions and checked against complete
canonical evidence before ranking. The [freeze receipt](../tests/fixtures/retrieval/freeze.json)
pins both question-file hashes, the chunk snapshot and held-out expected chunk
IDs. Tampering or absent/changed expected evidence stops evaluation.

All 48 quality questions search the full corpus. A natural document selector
is resolved and removed for this run; expected `document_ids` are evidence
labels, never implicit search restrictions. Separate selected-document runs
exercise the original names and explicit scopes. The 104 checks per strategy
cover full-corpus searches, selected-document isolation, unknown/mixed/
unavailable/ambiguous/conflicting selections, official-only scope, absent tiers
and broad-comparison budgets. Tests additionally inject invalid rights,
roles, audiences, tiers, families and versions into isolated Chroma fixtures.

Only regression results informed the configuration decision. The planned
20/20 branch budgets, RRF 60 and BM25 settings were retained before evaluating
the frozen held-out set. Subsequent verification corrected full-corpus versus
named-scope evaluation and added reporting/validation checks without changing
questions, expected evidence, scoring, chunking or ranking parameters.

| Strategy | Regression hit@5 | Held-out hit@5 | Held-out MRR@5 | Eligible |
| --- | ---: | ---: | ---: | --- |
| BM25 | 16/16 | 31/32 | 0.878 | Yes |
| Dense BGE-small | 15/16 | 32/32 | 0.984 | No |
| Hybrid RRF | 16/16 | 32/32 | 0.984 | Yes; selected |

Dense missed `dining-sodium`: its top five did not contain the complete
restaurant “Make special requests” passage with the salt/MSG qualifications.
BM25 missed held-out `finned-fish`, retrieving other temperature guidance
instead of the complete finned-fish safety unit. Hybrid returned complete
evidence for all 48 cases. Every mode had zero scope leakage and complete
provenance resolution. The [review report](retrieval-review.json) preserves
metrics/failures by category and evidence type, machine details and artifact
identities. Full per-case audits remain in the ignored staging reports.

Any eligible strategy must pass 16/16 regressions, at least 29/32 held-out hits,
scope isolation and complete provenance. Selection uses held-out hit count,
then MRR@5 rounded to three decimals, then BM25/dense/hybrid preference. Hybrid
wins on held-out hits over passing BM25; dense is excluded by its regression
failure. Selection rechecks configuration identity, comparable snapshots/
budgets/freeze receipts and metrics/gates against complete case results.

## Performance and artifacts

On this macOS ARM64/Python 3.13 machine, warm median end-to-end query latency
was approximately **17 ms BM25, 21 ms dense and 22 ms hybrid**. Peak isolated
process memory was approximately **179 / 626 / 632 MiB**, respectively. Paired
SQLite/Chroma storage was **11.50 MiB** for every strategy; BM25 keeps the
existing vectors. Validated configuration/index startup took about **3.5–3.6
seconds**; first dense/hybrid queries additionally loaded local weights in
about **2 seconds**. Model cache size is separate from paired index size.
These are one warm pass over 48 questions, not load/concurrency benchmarks.
Reports include p95 latency, embedding cost, category/type breakdowns, peak
RSS and exact dependency/machine versions. File caching and machine load can
change timings; no strategy tuning used performance or held-out failures.

The output directory contains each strategy report, `selection.json`, an
immutable selection-report-ID copy and a canonical versioned configuration:

```sh
dietary-rag search-corpus "What special requests reduce restaurant sodium?" \
  --retrieval-config data/staging/retrieval/p3-review/retrieval_config_97fa98ecad801e8185b67254aa0e17a48b4601622a3dcc0e89717000b51be231.json
```

The configuration pins resolver/registry identity, strategy, lexical/scoring,
fusion, deduplication, scope policy and budgets. Its artifact also pins the
compatible corpus snapshot. An incompatible snapshot, changed configuration
hash, resolver/registry change or conflicting explicit strategy fails before
querying. Both candidate artifact and selection remain `active: false` as
immutable historical records. P3.7 separately validates and atomically activates
the passing pair, pins in-flight requests and supports rollback; this evaluation
command does not publish or replace an active index. Current activation comes
from the lifecycle pointer and `corpus-status`.

The identical selected artifact is also checked into
[`config/retrieval-hybrid.json`](../config/retrieval-hybrid.json), so
`--retrieval-config config/retrieval-hybrid.json` works with this exact reviewed
candidate. Rebuilding different chunks or updating registry/resolver settings
requires a new compatible comparison and configuration.

## Validation boundaries

Unit/integration tests cover scoped lexical statistics, rank-only fusion,
name/alias failures, canonical resolution, branch audits, multi-document caps,
deduplication, table/qualification preservation, bounds/deadlines, model/config
mismatches, malicious vector metadata, frozen-case tampering and selection
ties/no-pass behavior. Existing compatibility tests reject corrupted paired
snapshots and exclude comparison-only/quarantined evidence. Real Chroma
storage tests use clearly labeled deterministic test vectors; the selection
report uses actual pinned BGE-small query embeddings and reviewed vectors.

This is a small project-authored diagnostic with literal complete-fact and
qualification matching, not independently external evaluation or generated
answer accuracy. Frozen held-out cases must not be reused for tuning. No
generation, answer-support threshold, reranker, query expansion, hierarchical
retrieval or rechunking is added. Policy and grounded answering follow in
Phases 4–5 after snapshot lifecycle work.
