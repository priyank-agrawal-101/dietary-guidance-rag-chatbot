# Phase 2.5: Structure-aware chunking

P2C.1–P2C.3 are implemented. The reviewed default corpus produces **269 searchable evidence chunks** from 443 canonical blocks across 16 sources. This completes the Phase 2 ingestion development gate. Vector embeddings, the released retrieval index and chatbot answers remain Phase 3 onward.

## In simple terms

A chunk is a small piece of source material that a search system can return. A useful chunk contains a complete piece of advice, including the conditions that make it correct. For example, “refrigerate within two hours” must travel with “within one hour above 90°F.” Cutting every document into equally sized pieces could separate those statements.

The chunker follows the structure already reviewed during extraction: sections, PDF cards, list items and table rows. Related paragraphs can share a chunk. A small food-group card keeps its own identity. Headings describe the evidence but do not become empty search results. Large lists split between complete top-level items; nested explanations and original numbering stay attached. List introductions repeat when necessary.

Tables become deterministic labeled text. A storage entry says which food, which refrigerator temperature and days, which freezer temperature and months, and what qualification applies. A blank source cell is explicitly marked `[blank in source]`; its underlying value stays blank. No missing value or summary is generated.

The model reads **tokens**, which are pieces of words and punctuation, rather than ordinary word counts. We now use E5's actual tokenizer. The preferred size is 350 tokens, including the document/section/audience context. Short complete evidence stays short. The hard limit is 512 tokens. If an indivisible recommendation or row cannot fit, its build is quarantined instead of being truncated.

Every chunk keeps a trail back to the canonical block and original HTML or PDF location. A reader can check the exact words, headings, table cells and page rectangles that produced a result.

## Technical foundations

`StructureChunker` accepts a normalized artifact and its original native export. It validates both contracts and replays normalization with the exact review receipt. The CLI additionally checks the native file's SHA-256 against the reviewed artifact hash. Pending or quarantined normalization is unavailable. Consumer builds exclude alternate comparisons, synthetic fixtures and sources that fail current registry eligibility. Comparison builds require an explicit separate role. Reviewed test fixtures have a separate API role; the consumer CLI cannot select them.

The pinned tokenizer/model revision is `f52bf8ec8c7124536f0efb74aca902b2995e5bcd` for `intfloat/e5-base-v2`. [The manifest](../config/tokenizer-e5-base-v2.json) pins six tokenizer/metadata files by SHA-256. Model position embeddings, Sentence Transformers' maximum sequence length and tokenizer configuration independently agree on **512**. `tokenizers==0.23.3` is hash-locked. Truncation and padding are disabled. Token counting includes `passage:`, compact document/section/audience context and the `[CLS]`/`[SEP]` special tokens. Only tokenizer files are downloaded; no embedding weights or LLM calls are required.

Embedding input has this shape:

```text
passage: Document: <original document name>
Section: <last compact original section heading, when available>
Audience: <original intended audience>

<exact prose/list slices or fully labeled table rows>
```

All heading paths remain metadata; unusually long extracted headings are omitted from the compact prefix. Full document metadata retains publisher, dates/edition, URLs, audience, family, role and original raw-file hash. A build records normalization/extraction IDs, chunker version, tokenizer identity and budgets.

### Boundaries and fidelity

| Evidence | Implemented boundary |
| --- | --- |
| Related prose | Same heading path and reviewed native region; split large prose at sentence boundaries. |
| Conditions/exceptions | Dependent sentences/paragraphs stay with their preceding recommendation, including hot-weather conditions. Unfittable atomic groups quarantine the build. |
| Prose overlap | Repeat complete trailing sentences/condition groups up to 50 body tokens; reduce overlap when needed to fit the next chunk. |
| Lists | Keep a short native list together, including HTML sibling `<li>` blocks from the same original container. Split large lists between top-level items. Repeat exact introductions; preserve children, qualifiers and numbering. |
| Independent PDF cards | Native layout region keys constrain merging, even when heading paths are identical. HHS food-group and NHLBI dining cards remain separate. |
| PDF pages | A spanning native block keeps contributing pages/rectangles. Explicit reviewed continuation keys allow cross-page merging. Page numbers alone do not cut a recommendation. |
| Tables | Repeat original title, headers/units, categories and notes. Row references resolve to native cells or original PDF table parts. No prose overlap. |

Sentence/condition boundaries use deterministic rules and reviewed layout structure. They do not claim general semantic understanding of arbitrary documents. New layouts and new exception wording need source review and regressions; the accepted corpus and critical examples are verified.

**Minnesota storage:** adjacent rows share a chunk only within the same category and budget. Uncategorised products keep independent boundaries. The original four-column expanded HTML grid remains available, including duplicate `Product` labels and blank cells. The two-column PDF alternate retains its own native representation and attribution.

**CDC pregnancy:** each of the nine food-category rows gets one chunk. Riskier and safer choices, pregnancy context and the original introductory note travel together.

**Minnesota grilling:** all six rows fit in one chunk, with the home-versus-regulated-facilities qualification repeated. Larger tables split into coherent row groups while retaining the note.

### Persistence and inspection

`data/staging/chunks.sqlite` is a separate versioned SQLite store. It refuses the acquisition catalog and unfamiliar schemas. Transactions store canonical blocks, normalization/native exports, chunks, immutable build records and explicit snapshot membership. An unsuccessful or quarantined batch cannot create a snapshot. Existing evidence cannot be overwritten under the same identity.

Chunk IDs hash the reviewed normalization, source references, exact text/context and chunking configuration. Build and snapshot IDs also hash their complete contents. Repeated inputs produce the same IDs. Budget or evidence changes create different identities. Multiple experimental snapshots coexist; reports select the precise build set, and `active: false` records that these are staging artifacts.

Each prose/list span is an exact start/end slice in a canonical block. Table chunks reference the original table and canonical row indices; those rows preserve native row/page identifiers. Source locations are checked against contributing blocks. PDF native exports retain word IDs and region maps, so geometry can be traced beyond heading paths.

For example, use the repository API to resolve a chunk:

```python
from pathlib import Path
from dietary_rag.corpus.chunk_repository import ChunkRepository

with ChunkRepository(Path("data/staging/chunks.sqlite"), read_only=True) as store:
    evidence = store.resolve_chunk("<chunk_id from a build JSON>")
    chunk = evidence["chunk"]
    normalized = evidence["normalization"]
    native = evidence["native"]
    # chunk.spans -> normalized.blocks -> normalized.decisions -> native.blocks/layouts
    # chunk.table_rows -> normalized.tables.rows -> original native cells/table parts
```

The [inspection examples](examples/chunking/inspection.json) include real storage, pregnancy, grilling, label and independent-card chunks with exact provenance. The [review report](chunking-review.json) records all source builds, token distributions, snapshot identities and the evaluation result. Full corpus exports and tokenizer caches stay in ignored staging directories.

## Reproduce the build

Install the existing extraction dependencies and the new small chunking lock. From an activated environment at the repository root:

```sh
python -m pip install --require-hashes -r requirements/chunking.lock
dietary-rag prepare-tokenizer
```

Preparation is the explicit network step. It caches the pinned files under `data/staging/tokenizers/e5-base-v2`; verified existing files are reused. Subsequent chunking, comparisons and tests work offline. Missing or changed tokenizer files fail closed.

Use the exact reviewed normalization artifacts described in [Phase 2.4](phase2-normalization.md). For this workspace their batch report is `data/staging/normalization/reviewed-report.json`. Recreating it requires the original reviewed acquisitions/extractions and exact matching receipts; a new unreviewed extraction is insufficient.

```sh
dietary-rag chunk-sources \
  --normalization-report data/staging/normalization/reviewed-report.json \
  --report data/staging/chunks/350-report.json

dietary-rag chunk-sources \
  --normalization-report data/staging/normalization/reviewed-report.json \
  --target-tokens 250 \
  --report data/staging/chunks/250-report.json

dietary-rag compare-chunks \
  --baseline-report data/staging/chunks/350-report.json \
  --candidate-report data/staging/chunks/250-report.json \
  --cases tests/fixtures/chunking/queries.json \
  --report data/staging/chunks/evaluation.json
```

Comparison evidence requires `chunk-sources --comparison`, preferably with `--output-dir data/staging/chunks/comparison` and its own report. It creates a separate comparison snapshot and skips the default sources. Default builds skip comparison evidence based on the actual normalized contract, irrespective of informational report flags.

`--database`, `--output-dir`, `--tokenizer-dir`, `--tokenizer-manifest` and `--project-root` can be specified explicitly. `--target-tokens` and `--overlap-tokens` override the settings for an experimental build. Successful builds return 0; evidence rejection/quarantine returns 1; invalid settings/command constraints return 2.

## Results and trade-offs

| Setting | Chunks | Prose / list / table | Full input tokens: min / median / max | Complete evidence hit@5 |
| --- | --- | --- | --- | --- |
| 350 target, 50 overlap | 269 | 96 / 152 / 21 | 20 / 61 / 337 | 16/16 |
| 250 target, 50 overlap | 274 | 98 / 154 / 22 | 20 / 61 / 263 | 16/16 |

The 250 run contains one 263-token indivisible pregnancy row. That is valid: 250 is a preference, while 512 is the enforced limit. Most chunks are short because complete lists/cards/rows are short and remain independently searchable.

The 16 authored questions cover cooking temperatures/rest periods, storage and source blanks, hot-weather exceptions, pregnancy choices, Nutrition Facts labels, produce handling and dining tips. The offline diagnostic uses deterministic **BM25**, with full context plus body indexed and top-five results checked for all required original phrases in a single attributable chunk. It verifies complete-evidence hits, not generated answers. Every expected item must exist in each build or evaluation fails.

**Keep 350/50 as the baseline.** This small lexical diagnostic found no advantage for 250. Dense E5 retrieval, representative held-out questions, precision and answer/citation quality still need Phase 3/7 evaluation before changing defaults. No semantic LLM splitter or hierarchical retrieval was added.

The three approved comparison sources produce 42 separate chunks. The original OCR fixture's uncertain numeric candidate remains quarantined, and the complete normalized OCR export is rejected by consumer chunking even though four clear candidates exist.

Verification covers stable identities, exact body/row coverage, model limits, sentence/condition overlap, nested lists/introductions, region separation, table notes/blanks, source eligibility, original-file hashes, SQLite transactions and reproducible CLI exports. Real-tokenizer tests skip explicitly if preparation has not been run; deterministic boundary tests still run without downloads. The verified workspace ran all 336 tests with the real pinned tokenizer and existing extraction fixtures.
