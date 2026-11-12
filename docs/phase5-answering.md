# Phase 5: grounded answering and citation validation

P5.1–P5.7 are implemented. The shared `AnswerService` turns an allowed, pinned retrieval request into an evidence decision, a structured draft, validated claims and code-authored citations. `answer-question` provides the cited CLI development workflow. P6.1–P6.3 now expose this workflow through the [Groq API integration](phase6-api.md). P6.4–P6.6 now expose the [browser UI](phase6-ui.md).

The original Phase 5 default is an offline **extractive** generator and an independent extractive entailment verifier. It keeps complete source wording, category/value pairs and attached qualifications, and requires no additional model or credentials. The typed adapters support an injected generation provider and a separate semantic provider; no hosted provider is enabled implicitly. The extractive verifier deliberately withholds free-form paraphrases. This is a working conservative development answer path, not a claim of broad semantic coverage or completion of Phase 7 evaluation.

## Run and inspect an answer

Repository settings now select Groq; append `--offline` to the supported-answer commands below to reproduce the original Phase 5 extractive behavior without credentials. See the [API guide](phase6-api.md) for `.env` setup and hosted token/usage budgets.

Use the compatible active corpus published in Phase 3:

```bash
.venv/bin/dietary-rag answer-question \
  'How long can hard cooked eggs stay in the refrigerator or freezer?' \
  --documents mn_storage_chart --format text \
  --report data/staging/answering/eggs.json

.venv/bin/dietary-rag answer-question \
  'What internal temperature should chicken be cooked to?' \
  --documents cdc_food_safety mn_safe_grilling_pdf --format text

.venv/bin/dietary-rag answer-question 'What diet treats diabetes?'
.venv/bin/dietary-rag answer-question 'How much protein is in an egg?'
```

The default output is the typed JSON response. Text output groups claims by document and attaches each complete citation immediately to its claim: document name, publisher, jurisdiction, source tier, honest date/edition label, section, physical/printed page details and source links. Exit code 0 includes conversational refusals, clarification and evidence gaps; service failures exit 1. The existing administrative `search-corpus` command still inspects retrieval alone.

The original-byte store, corpus pointer, tokenizer and embedding model are opened lazily after request/policy permission. A refusal works even with an absent corpus/model directory. The active pointer is pinned once and the evidence context remains open through validation and citation rendering. Answer requests neither ingest documents nor fetch source URLs.

## Evidence decisions

`EvidenceAssessor` evaluates explicit question aspects and complete retrieved evidence units. It never treats a similarity score as proof of support. The local profiles cover cooking/rest temperatures, refrigeration/freezing durations, time out in hot weather, pregnancy food choices, label interpretation, washing/pre-washed produce, restaurant sodium requests, general food selection and household handling/separation.

Storage checks retain the food/category, raw/cooked state, opened/unopened distinctions, requested method and stated temperature. Generic egg requests require preparation/storage details rather than transferring hard-cooked or separated-egg values. Unknown foods and unmatched temperatures remain gaps. A refrigerator request cannot borrow a freezer-only value or a blank table cell. Table units contain the complete row, title, headers and notes; an original source blank stays explicitly blank. Prose temperature charts are recognized as complete category/value/rest-condition pairs rather than applying another category's number. Other contextual prose retains attached warnings, including the caution about unclean surfaces when washing pre-washed produce. Source audience metadata prevents adult-only guidance being used for children and the reverse.

The assessor records `supported`, `partial`, `needs_details` or `not_covered`, with per-document/aspect coverage and selected unit IDs. Only supported units enter generation. A partly supported comparison answers the supported document/aspect and names the remaining gap with the actual searched scope. Missing essential details produce a fixed clarification and no generation. A successful search without sufficient evidence produces a fixed gap message naming **all searched documents**, including those without top hits. It does not supply background model knowledge or broaden the search. Unknown/unavailable selections and corpus/model failures remain distinct service errors.

These are intentionally bounded local profiles. They are not a general natural-language entailment system. Unrecognized questions may ask for clarification or receive a conservative evidence gap even when a human could answer from other corpus passages. Retained contextual excerpts can be longer than a fluent paraphrase; preserving qualifiers takes priority in this development implementation.

## Provider and draft contracts

`GenerationInput` supplies the resolved question, pinned snapshot ID, approved evidence groups, U.S. applicability/source-tier metadata, fixed instructions, expected gaps and output schema. Source and user content are quoted data. The generator has no external tools and cannot author URLs, dates, publisher names or an unsupported summary.

`DraftAnswer` groups claims by exactly one document. Each claim supplies its text and retrieved chunk/support-quote pairs. Typed draft gaps use application-issued aspect IDs and controlled reasons; they must exactly match application-assessed gaps. A model cannot invent a searched-document list or turn a supported aspect into a gap. Independent evidence units cannot be merged into one compound claim. The application enforces claim/document limits and keeps any answer-budget omissions explicit.

`TypedGeneratorAdapter(provider, timeout_seconds=...)` validates a JSON-compatible payload or JSON string. The provider receives the constrained input and a timeout budget. Exceptions become sanitized `model_unavailable`, timeouts become `request_timeout`, and malformed output is a repairable draft-validation failure. `TypedSemanticVerifierAdapter` provides the separate typed entailment boundary: supported flag, finite confidence and an entailment/contradiction/insufficient/uncertain reason. Uncertain, low-confidence or contradictory verdicts cannot authorize a claim. Malformed semantic output or verifier outages are dependency failures.

For a provider-backed experiment, instantiate the shared service explicitly:

```python
from dietary_rag.answering.adapters import TypedGeneratorAdapter, TypedSemanticVerifierAdapter
from dietary_rag.answering.service import AnswerService

service = AnswerService(
    policy_runtime,
    settings=settings.answering,
    generator=TypedGeneratorAdapter(generate_payload, timeout_seconds=30),
    verifier=TypedSemanticVerifierAdapter(verify_payload, timeout_seconds=15),
)
response = service.answer({
    "question": "What internal temperature should chicken be cooked to?",
    "scope": {"mode": "selected", "document_ids": ["mn_safe_grilling_pdf"]},
})
```

Both callables accept a JSON-compatible input and `timeout_seconds` keyword; they must enforce their own I/O timeout and provide no unrelated tools. The service checks stage and total elapsed deadlines but cannot interrupt an arbitrary blocking synchronous callable. Setting `answering.generator: configured` without supplying a generator yields a model service error after adequate evidence; it never silently produces unsupported text. Defaults, confidence, claim budgets, stage timeouts and the zero-or-one repair limit are validated in `AnsweringSettings`.

## Validation, repair and citations

Every displayed claim passes this ordered validation:

1. Exact draft schema, permitted document scope and pinned snapshot/release/generation identity.
2. Actual retrieval membership and canonical chunk/document-version/text-hash resolution in the same evidence context.
3. Reviewed U.S. consumer applicability, rights and one-document support separation.
4. Exact support quotes after **Unicode whitespace collapse only**; case, punctuation and numbers are not normalized away.
5. Complete approved contextual-unit support; dropped titles, row labels, warnings or notes cannot be replaced by a shorter but technically matching quote.
6. Original numeric sequences, units and unit labels, including duration headers; no unit conversion is performed. Negations and qualifiers must remain present.
7. Separate semantic entailment verification. The default proves unchanged extraction of the approved complete unit; a free-form paraphrase requires an explicitly injected verifier and still passes all deterministic checks.
8. Reusable post-generation policy, then structural response checks and final policy verification before display.

A semantic provider's positive verdict cannot override incorrect numbers/units, a scope violation or prohibited advice. Provider agreement alone is not a universal grounding guarantee; unfamiliar paraphrases still need independent review. Tests include a deliberately permissive semantic provider to demonstrate that unit changes and medical additions remain blocked by code.

At most one repair uses a fresh copy of the **same authoritative evidence** and a fixed validation code. It performs no new retrieval or scope expansion and passes no raw failed draft back as instructions. Provider mutation cannot change the validator's evidence input. Repeated invalid drafts produce `answer_validation_failed`; adequate evidence plus a model/verification failure is never mislabeled `not_covered`. Failed claims and citations are withheld entirely.

Citations are authored from stored document metadata and reviewed original locations, never model fields. Unknown publication years stay null; source-stated dates and editions are shown honestly. HTML links use observed anchors only. PDF links use one-based physical pages, list every page covered by the source chunk, and provide an individual `#page=N` URL for each; printed labels are separate and cannot determine indexes. Chunk locations are conservative: if a chunk spans several original locations, the citation retains all of them. Differences between documents remain attributed separately, without averaged temperatures or merged recommendations.

## Inspection and verification

Verification passed: **576 full-suite tests**, **43 focused Phase 5 tests**, **152 final policy/contract/lifecycle/settings/import checks**, and **31/31 active-corpus review cases** with citations from all six PDFs. Ruff lint/format, dependency consistency, whitespace checks, the wheel build and wheel CLI refusal smoke also passed. [Compact examples](examples/phase5-answers.json) retain supported, partial, gap, clarification, failure and repaired outcomes.

`--report` saves the response, actual search audit, evidence assessment, attempt/validation codes, final validated draft and original claim-evidence provenance. Each cited chunk includes its document version, raw/text hashes, original locations (including PDF geometry), prose block spans or table row identity. Raw failed provider drafts and raw provider exceptions are not written into the report. Ordinary `answer()` calls do not write reports or log health questions.

[answering-review.json](answering-review.json) retains the active-corpus demonstrations and [the case file](../tests/fixtures/answering/review.json) records expected states and required evidence values. The review covers the 16 retrieval regressions, all six active PDFs, two-document comparison, a document-specific gap, missing details, source blank, refusals, unknown/unavailable selections, invalid numeric drafts, successful bounded repair, generation outage and timeout. Fault-injection providers in the review script are explicitly labeled and are never the CLI default. The artifact pins the release, corpus snapshot and case-file hash.

Reproduce the review and tests:

```bash
.venv/bin/python scripts/inspect_answers.py \
  --output data/staging/answering/review.json
.venv/bin/pytest tests/unit/test_answering.py tests/integration/test_answer_cli.py -q
.venv/bin/ruff check src scripts/inspect_answers.py tests
.venv/bin/ruff format --check src scripts/inspect_answers.py tests
```

The review is development evidence, not a released chatbot or Phase 7 semantic evaluation. Synthetic unit/integration fixtures are clearly labeled; the retained answer review itself uses the real approved active corpus and BGE/hybrid retrieval. API/UI integration and broader release evaluation remain Phases 6–7.
