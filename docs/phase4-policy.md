# Phase 4: policy and response contracts

Phase 4 implements P4.1–P4.6. It permits reviewed general US food-selection and household food-safety questions to reach retrieval, declines medical advice and calorie/weight targets, and routes individual-food nutrient values to the future Milestone 3 capability. It does not generate dietary answers: evidence sufficiency, grounding, quote validation and code-rendered citations belong to Phase 5; HTTP endpoints and UI belong to Phase 6.

## Routing and classification

`policy/rules.py` normalizes Unicode and checks explicit prohibitions before classification. A deterministic prohibition cannot be overridden by a classifier, a source selection, retrieved text or a generator. Mixed requests with a prohibited portion are withheld as a whole; fixed templates ask for a separate permitted question. Medical and calorie/weight refusals refer users to a qualified clinician or registered dietitian. Nutrient lookups explain Milestone 3 without estimating values. All these responses contain no citations or claimed search history.

`LocalIntentClassifier` is an offline conservative implementation, requiring no credentials, downloads or network calls. It recognizes common general guidance and requests context for unresolved intent or unspecified food/storage situations. Words such as calories, fat or weight alone do not cause refusal: nutrition-label explanations, food-group portions and cooking temperatures remain allowed. This is a bounded prototype classifier, not a trained semantic model or proof of universal paraphrase detection. Unrecognized requests receive clarification. The curated cases demonstrate known behavior, and future classifier changes require regression review.

`TypedClassifierAdapter` accepts a callable returning the exact `IntentClassification` payload: nonempty unique intents, finite confidence and an optional context-needed flag. It rejects unknown fields and malformed output; provider text cannot become a user-visible explanation. Application code maps output to routes. Uncertainty never becomes permission, mixed intents prioritize prohibitions, and low confidence cannot weaken a positive prohibition. Exceptions produce sanitized `model_unavailable`; timeouts produce `request_timeout`. The default settings select `local_conservative_v1`, minimum confidence 0.9 and a ten-second classification deadline. Injected I/O providers must enforce their own timeout; the elapsed-time check rejects late completion but cannot interrupt a blocking synchronous provider.

Inspect routing without opening the corpus:

```bash
.venv/bin/dietary-rag check-policy 'How should I store leftovers?'
.venv/bin/dietary-rag check-policy 'What is my ideal weight?'
.venv/bin/dietary-rag check-policy 'How much protein is in an egg?'
```

`check-policy` is an administrative inspection command. `search-corpus` remains a raw administrative retrieval inspection command; it is not a chat or answer route.

## Contracts and request gate

`api/contracts.py` defines `ChatRequest`, discriminated `all`/`selected` scopes, document-grouped claims, HTML/PDF citation structure, five conversational states and a separate `ServiceError` contract. Defaults are 2,000 question characters, 20 unique selected IDs and 20 candidate hits, configurable through runtime settings. Empty selection, unknown fields, invalid IDs, contradictory filters and oversized input fail before policy or resources. Duplicate selected IDs are normalized before orchestration. Source tiers can only narrow approved evidence.

Response invariants require actual search scope for `not_covered`, claims and document-matching citations for `answered`, and fixed messages without claims/citations for non-answer states. PDF citations require physical pages; unknown source dates remain null. These are structural requirements; accepting this schema alone does not validate factual support. Service errors map to 422 invalid request, 404 unknown document, 409 unavailable source, 503 corpus/model unavailable, 502 answer validation failure and 504 timeout. Resolver issues retain unknown versus known-unavailable selections without falling back to the full corpus. Ambiguous/conflicting document names receive clarification.

`PolicyRuntime.prepare` validates the complete request, applies policy, lazily opens retrieval, and yields either a fixed `ChatResponse`, a `ServiceError`, or an internal `PreparedRequest`. A successful search, including a search with no hits, becomes a preparation result: only Phase 5 evidence assessment may decide whether guidance actually covers the question. Request IDs are created by application code and provider exceptions are not exposed. Context cleanup runs even when the consumer raises; answering exceptions are not incorrectly translated to corpus failures.

Use the active-corpus adapter in the future answer orchestrator:

```python
from dietary_rag.policy.runtime import PolicyRuntime, PreparedRequest
from dietary_rag.policy.service import PolicyService

runtime = PolicyRuntime.for_active(
    lifecycle_store, registry, tokenizer, processor, resolver, settings.runtime,
    policy=PolicyService(settings.policy),
)
with runtime.prepare({
    "question": "How should I store leftovers?",
    "scope": {"mode": "selected", "document_ids": ["mn_storage_chart"]},
}) as outcome:
    if isinstance(outcome, PreparedRequest):
        # Phase 5 assesses outcome.search_result, generates and validates claims
        # against outcome.evidence_request.resolve_evidence(chunk_id), then calls
        # runtime.check_response_policy(validated_response) before display.
        pass
    else:
        response_payload = outcome.model_dump(mode="json")
```

The adapter reads the active pointer once, after policy permission, and retains the pinned request through evidence resolution. An absent/incompatible corpus is a service failure. Query embedding dependency failures are typed independently from corpus-integrity failures. No request fetches source URLs, uploads documents or acquires new sources.

## Post-generation policy and review

`PolicyService.check_claims` and `PolicyRuntime.check_response_policy` are the mandatory reusable Phase 5 hooks. They withhold numeric calorie/weight advice, medical treatment assertions, individual-food nutrient assertions, bypass instructions and uncertain policy scope, checking each claim separately. General handling/selection claims can pass. They do not prove grounding, applicability, quote matches or citation provenance; Phase 5 must validate those independently before rendering. The hook may conservatively withhold unfamiliar allowed wording rather than assuming permission.

The [review report](policy-review.json) records 36/36 routing cases, 98 focused Phase 4 tests, the 529-test full-suite run and 153 passing tests after the final policy/retrieval refinements. Ruff lint/format, dependency consistency, whitespace checks and the wheel build passed.

The versioned review set in `tests/fixtures/policy/cases.json` includes explicit requests, paraphrases, mixed medical/nutrient/allowed intent, Unicode bypass attempts, label/portion/temperature near misses, ambiguity and named-document requests. Tests also exercise malformed/uncertain/failing adapters, fixed messages, all response states, configured limits, prohibited generated claims, actual Chroma/BM25 scope isolation and active-request evidence pinning. Synthetic vectors/proof in small integration fixtures are labeled explicitly; they validate gates and lifecycle, not semantic retrieval quality or release readiness.

Run the focused checks with:

```bash
.venv/bin/pytest tests/unit/test_policy.py tests/unit/test_api_contracts.py tests/integration/test_policy_runtime.py -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
```
