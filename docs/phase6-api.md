# Phase 6.1–6.3: API integration with Groq

P6.1–P6.3 expose the existing validated answering flow through FastAPI. Both `answer-question` and `serve` construct the same lazy active-corpus runtime and `AnswerService`. The repository configuration now selects Groq's `openai/gpt-oss-120b`; `--offline` explicitly retains the Phase 5 extractive workflow. P6.4–P6.6 now provide the [chat UI](phase6-ui.md) at `/`; broader release evaluation remains Phase 7.

## Start the API

Install the API lock alongside the existing embedding/tokenizer dependencies and prepare/publish the compatible corpus using the Phase 3 workflow:

```bash
.venv/bin/python -m pip install --require-hashes -r requirements/api.lock
# Create .env if absent, then edit its GROQ_API_KEY value locally.
.venv/bin/dietary-rag serve
```

Add `GROQ_API_KEY=your-key` to the project-root `.env`. The file is ignored by Git. `serve` and `answer-question` explicitly read it; exported environment variables take precedence, and `.env` interpolation is disabled. `--env-file path` selects a different file. Keys are supplied directly to the provider, never added to the settings model or inspection reports. Restart the service after editing the key. An absent key is a typed `model_unavailable` failure when generation is needed; policy refusals and document listing still work.

The server binds to `127.0.0.1:8000` by default. Access logging is disabled so questions and provider failures are not written by the CLI server. This is a local prototype; authentication remains outside this subphase; the browser client is implemented in P6.4–P6.6.

```bash
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
curl http://127.0.0.1:8000/documents
curl http://127.0.0.1:8000/usage
curl http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"question":"How long can hard cooked eggs stay in the refrigerator or freezer?","scope":{"mode":"selected","document_ids":["mn_storage_chart"]}}'
```

Omit `scope` for full-corpus retrieval. The response uses the existing `ChatResponse` contract: source-separated claims, canonical citations, actual searched documents and explicit gaps. `/docs` and `/openapi.json` describe the API models. HTTP streaming is disabled; only completed validated answers are returned.

The CLI uses the same Groq configuration and persistent quota ledger:

```bash
.venv/bin/dietary-rag answer-question \
  'What internal temperature should chicken be cooked to?' \
  --documents cdc_food_safety mn_safe_grilling_pdf --format text

# Explicit credential-free reproduction of the conservative Phase 5 behavior:
.venv/bin/dietary-rag serve --offline
.venv/bin/dietary-rag answer-question \
  'How long can hard cooked eggs stay in the refrigerator or freezer?' \
  --documents mn_storage_chart --format text --offline
```

## Endpoint behavior

| Endpoint | Behavior |
| --- | --- |
| `GET /health/live` | Process liveness, without opening corpus, models or credentials. |
| `GET /health/ready` | Pins and fully validates the active snapshot and local retrieval compatibility; reports snapshot/release/generation and available document count. Missing generation credentials fail readiness. No paid provider probe occurs. |
| `GET /documents` | Registry source metadata plus active availability. Available sources use the pinned document's metadata. Absent/incompatible corpus returns 503 with all sources unavailable and a typed corpus error. |
| `POST /chat` | Runs existing policy, pinned retrieval, assessment, bounded generation/repair, canonical validation and citation rendering. |
| `GET /usage` | Aggregate local rolling minute/hour/day usage, reservations, limits and provider cooldown; no questions, keys or provider payloads. |

Liveness does not imply readiness. Readiness is a local compatibility and credential check, not a guarantee that Groq is reachable or has remaining account quota. A corpus refresh is observed on the next request; each individual request uses one pinned snapshot throughout. HTTP and CLI never ingest/fetch sources during chat.

Every HTTP response has a generated UUID in `X-Request-ID`; chat/error/list bodies carry the same ID. Caller-supplied IDs cannot replace it. A single absolute monotonic deadline starts at HTTP entry, including request-body reading and worker waiting, and propagates through policy, retrieval, generation, repair and validation. The CLI starts the same deadline at `AnswerService.inspect()`.

A pure ASGI boundary returns 504 promptly when work exceeds the deadline. Worker slots stay occupied until their work actually finishes, preventing abandoned requests from creating unbounded in-flight corpus work. The Groq async transport enforces the remaining total stage/request budget, bounded socket timeouts and a 64 KiB response cap. Synchronous local model/corpus work cannot be forcibly interrupted; it is bounded by two worker slots by default, and deadline checks prevent it starting later generation or returning a late answer. Request bodies are capped at 16 KiB, including chunked bodies.

| Error code | HTTP status |
| --- | --- |
| `invalid_request` | 422 |
| `unknown_document` | 404 |
| `source_unavailable` | 409 |
| `token_budget_exceeded` | 422 |
| `usage_limit_exceeded` | 429 with `Retry-After` |
| `corpus_unavailable`, `model_unavailable`, `service_busy` | 503 |
| `answer_validation_failed` | 502 |
| `request_timeout` | 504 |

Provider error bodies, raw exceptions, request validation input and partial drafts are withheld. An outage, exhausted quota or rejected draft never becomes an evidence-gap answer.

## Token optimization and usage limits

The first hosted path remains conservative extraction. Groq receives only the resolved question and complete assessed evidence text, once per unit, using short IDs (`u0`, `u1`, ...). It returns a strictly constrained JSON array of those IDs in document order. Code requires every approved unit exactly once, reconstructs the immutable source claims and support quotes, then runs the full Phase 5 validator and local extractive entailment verifier. Groq cannot supply factual text, citations, source metadata or new gaps. This saves output tokens and avoids sending duplicated support quotes, long hashes, draft schemas or a second semantic-model call. It does not add free-form paraphrasing or broaden the assessor's existing topic coverage.

The request sets `reasoning_effort: low`, `include_reasoning: false`, `temperature: 0`, `stream: false`, strict JSON-schema output, and `max_completion_tokens: 1024`. Hidden reasoning can still consume billed completion tokens; excluding its response field does not make it free. No HTTP retries, tools, redirects or scope expansion are enabled. The existing zero-or-one repair is used only for invalid structured selections; each attempt has its own reservation. A transport outage or 429 is not repaired/retried.

`groq` settings in `config/settings.yaml` are **local application ceilings**, independent of Groq's actual account/organization quotas:

| Budget | Default |
| --- | ---: |
| Input admission bound per call | 6,000 |
| Maximum completion tokens per call | 1,024 |
| Requests per rolling minute / hour / day | 30 / 60 / 300 |
| Tokens per rolling minute / hour / day | 8,000 / 30,000 / 100,000 |

Input admission uses a deliberately conservative UTF-8 byte bound over messages and schema, plus 256 tokens for chat-template overhead. It is an estimate rather than the model's exact tokenizer count, and can reject evidence that would fit Groq's context. The historical E5/BGE tokenizers are not appropriate for GPT-OSS generation. Complete evidence is never truncated to fit; a too-large request returns `token_budget_exceeded`, suggesting a narrower question or fewer documents.

Before dispatch, `data/usage/groq.sqlite` atomically reserves the input bound plus the maximum completion budget. Every call counts against requests, including failures and repairs. Successful responses settle with Groq's `usage.total_tokens`, which includes reasoning. Unknown usage after timeouts/outages/malformed responses conservatively retains the full reservation. Reservations and cooldowns survive restarts and concurrent CLI/API processes sharing this data directory. SQLite stores call IDs, timestamps, token totals and pending flags; it stores no prompts, questions, generated text or secrets.

All windows are rolling elapsed time: an hour means the preceding 3,600 seconds and a day means the preceding 86,400 seconds, without a midnight reset. Concurrent reservations are serialized with a SQLite transaction. Local rejections calculate when sufficient capacity expires. Provider 429 responses persist a shared cooldown from `Retry-After` (60 seconds if absent/invalid), expose a sanitized retry delay and dispatch no automatic retry. Usage outside this application or in a different data directory is not counted locally; Groq's account limit remains authoritative.

Checked against Groq's public documentation on November 12, 2026:

- [Models](https://console.groq.com/docs/models): GPT-OSS 120B, 131,072-token context and 65,536 maximum completion tokens; developer-plan limits differ from free-plan limits.
- [Rate limits](https://console.groq.com/docs/rate-limits): published free-plan limits for this model are 30 RPM, 1,000 RPD, 8,000 TPM and 200,000 TPD. Confirm the actual values in your account's limits page. Groq's token rate headers describe TPM; request headers describe RPD, not hourly usage.
- [Structured outputs](https://console.groq.com/docs/structured-outputs): strict JSON-schema decoding is supported for `openai/gpt-oss-120b`.
- [Reasoning](https://console.groq.com/docs/reasoning): GPT-OSS supports `include_reasoning`; `reasoning_format` is unsupported for these models.

## Verification

Verification passed: **628 full-suite tests**, followed by **44 final Groq/API checks** after the OpenAPI and ledger refinements. Ruff lint/format, dependency consistency, whitespace checks and wheel build passed. The [real active-corpus API review](api-review.json) reports readiness for 16 active documents, metadata for all 68 registry sources, an HTML storage answer, a two-document comparison with PDF citations, refusal/capability states and an unknown-selection error. Its two Groq calls use a mocked HTTP transport; source evidence and BGE/hybrid retrieval are real.

Deterministic mocked-provider tests cover the Groq wire payload, exact reconstruction, invalid/duplicate/missing IDs, truncated/oversized output, sanitized outages, no transport retries, missing keys, input budgets, timeout reservation retention, persistent 429 cooldowns, each independent minute/hour/day request/token window, and concurrent reservation races.

HTTP integration tests use a pinned synthetic SQLite/Chroma snapshot to check API/CLI citation parity, liveness/readiness separation, source metadata, absent/corrupt corpus behavior, policy-before-I/O, validation/body limits, selection errors, missing keys, mocked Groq answers, 429 usage state, stable request IDs, prompt deadline responses and bounded abandoned workers. No paid Groq request is part of these tests. Authenticated live verification now passes with the user-supplied key; see [live Groq review](groq-live-review.json).

```bash
.venv/bin/pytest tests/unit/test_groq.py tests/integration/test_api.py \
  tests/unit/test_answering.py tests/integration/test_answer_cli.py -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
```

## Authenticated Groq smoke test — November 12, 2026

The running local server passed six HTTP cases: medical refusal, structured-data capability, invalid request, unknown selection, an HTML storage answer and a two-source comparison with a PDF citation. The two supported answers matched the canonical claims, citations, searched documents and gaps in the earlier active-corpus review. Each response body request ID matched its HTTP header.

The four pre-generation cases made zero Groq calls and consumed zero tokens. The two answered cases made **two real Groq calls totaling 876 tokens**, as measured by the persistent ledger's usage delta. All **44 deterministic Groq/API tests** also passed, covering local quota exhaustion and provider errors without intentionally exhausting the live account. [The live report](groq-live-review.json) retains response evidence and aggregate before/after usage, with no credentials or raw provider payloads.

To reproduce while `dietary-rag serve` is running:

```bash
.venv/bin/python scripts/smoke_api.py
```

This script makes up to two supported live chat requests; each can use the existing single repair allowance. It stops further hosted tests if a check fails. It contacts the local API, never reads `.env`, and writes the review report. The browser UI is now implemented; see [the UI guide](phase6-ui.md).

The UI also adds `GET /ui/config` for public interface limits and `/assets/` for packaged static resources. P6.4–P6.6 serialize pinned corpus contexts because embedded Chroma clients share closeable resources; lock waiting obeys the same absolute deadline. See [UI verification](phase6-ui.md) for the regression checks.
