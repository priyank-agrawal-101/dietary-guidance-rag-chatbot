# Phase 6.4–6.6: chat UI and source comparisons

P6.4–P6.6 and the Phase 6 development exit gate are complete. The `web/` client is served by the existing Python API, uses the shared validated answer workflow, and requires no frontend build or Node runtime in normal operation. The conservative evidence assessor and extractive claim wording remain unchanged; broader semantic evaluation and release work belong to Phase 7.

## Open the chat

Restart the running server to load the new UI and corpus concurrency fix:

```bash
.venv/bin/dietary-rag serve
```

Open **http://127.0.0.1:8000/**. The API schema remains at `/docs`. The same `.env` Groq key and `config/settings.yaml` token/usage budgets apply. For credential-free development, run `serve --offline`. Installation from a wheel includes the HTML, JavaScript, stylesheet and leaf icon; the installed app can serve these assets even when the project root does not contain `web/`.

Choose **All available sources** for full-corpus retrieval. Choose **Choose sources** to select one document or compare two or more. Source search filters the checklist locally, preserves checked sources and does not dispatch a chat request. Only available documents can be selected; reviewed references outside the active collection appear in Diagnostics → Sources. Runtime character and selection limits come from `/ui/config`, which exposes only the three interface limits and no credentials.

Examples fill the question box without submitting. The footer opens `/diagnostics` in a separate tab to preserve the current conversation, with Status, Sources, Usage & limits, and API & debugging tabs. Status checks, corpus snapshots, unavailable references, rolling minute/hour/day call and token totals, pending reservations, provider cooldowns, and API links live there. Source and usage tabs load on demand; the chat never requests `/usage`. Loading any diagnostic check never calls Groq. Request IDs and corpus snapshots are absent from answer cards; citations and evidence gaps remain visible. The API tab explains finding request identifiers in the Network panel and JSON response body. This page is a presentation separation, with no authentication or access restriction. A chat submits exactly the current question and scope. Earlier conversation turns are not resent, saving tokens. Up to ten turns are retained in the current page's memory, with a clear button; reload clears them. The client uses no cookies or local/session storage for conversation history.

## Answers and every response state

Supported claims stay verbatim, including categories, units, conditions and source warnings. Each document has its own card; recommendations are never averaged or combined across sources. Desktop comparisons use two columns and stack on narrower screens. Each claim has an adjacent citation with document name, publisher, tier, jurisdiction, honest date label, section, original support excerpts, and approved source/page links. Physical PDF page numbers and printed page labels are separate.

A partial selected-source comparison retains a card for the document with no supported claims and shows its explicit evidence gaps. The UI removes the repeated `Searched:` suffix from each gap and lists the complete actual searched scope once in a disclosure. This changes presentation only; it does not broaden scope, invent coverage or rewrite claims. Source IDs are replaced with their known display names in gap labels when possible.

The interface displays all conversational states: answered, not covered, outside scope, nutrient lookup unavailable, and clarification needed. It also displays every typed service failure, including unknown/unavailable selections, corpus/model outages, validation failure, timeout, exhausted quota, oversized token budget and a busy service. Quota errors show the application's retry delay without scheduling an automatic retry.

Only a complete response passing client structural checks and matching the `X-Request-ID` header is rendered. These checks complement server validation; factual/policy validation remains on the server. Pending requests show a loading indicator, disable duplicate submissions and display no partial draft. Stop waiting aborts the client's wait, while clearing a pending conversation also prevents any late result from repopulating it. A browser timeout or network failure gets its own visible error. Stopping the client wait cannot guarantee already-started server/provider work is interrupted.

Diagnostics displays readiness details. The chat shows a brief availability notice only when sources or answers are unavailable, with a source-loading retry button. Refusal/capability requests can still reach the policy service during a corpus/model outage. A refresh preserves formerly selected but now unavailable IDs and blocks submission until the user explicitly clears or changes the selection; it never silently searches a smaller or broader scope.

## Safe rendering and browser behavior

All user, source and provider strings are inserted with `textContent` into constructed DOM elements. The renderer never uses `innerHTML`, Markdown execution or dynamic scripts. Malicious markup in titles, claims or excerpts remains visible literal text.

A citation link must be an absolute HTTPS URL, with no embedded credentials, and match the reviewed document's exact origin, path and query. Observed section/page fragments are allowed. Unknown documents, changed paths/hosts/queries, HTTP, protocol-relative, `javascript:`, `data:` and credential-bearing URLs get inert text instead of anchors. New-tab source links use `noopener noreferrer` and no-referrer policy. The client never manufactures PDF page URLs.

The chat page, diagnostics page, and their assets carry a restrictive Content Security Policy: same-origin scripts, styles, images and API connections; no inline script, external content, framing, forms to other origins or base-URL overrides. All API responses also have `nosniff` and no-referrer headers. The chat CSP does not interfere with the existing API documentation page. Only `web/assets/` is mounted publicly; project files, `.env`, tests and configuration cannot be read through that mount.

Labels, radio groups, checkboxes, semantic headings, focus indicators, a skip link, live response announcements and reduced-motion support are present. Native Chrome checks covered the real source checklist and cited two-source result at desktop size and **390 × 844** responsive size, with stacked answer cards, visible links and no clipped horizontal content. The desktop preview was restored afterward.

## Corpus concurrency fix discovered during UI verification

Chroma's embedded clients share a cached system which the existing `close_chroma` helper stops when a read context ends. Concurrent readiness and document-list checks could therefore close another request's resources and briefly report zero available sources. `ActiveRuntime` now serializes the complete pinned corpus context within the process, including evidence/citation validation. Waiting for that lock consumes the existing absolute request deadline; process liveness and local usage inspection do not acquire it. Worker limits and sanitized timeout/busy errors remain in force. Regression tests cover simultaneous corpus checks and deadline expiry while waiting for access.

## Verification and reproduction

Verification passed **634 full-suite Python tests** and **48 Node/jsdom UI tests**, plus Python/JavaScript formatting and syntax checks. A wheel was built and installed into a separate virtual environment, reusing the existing locked API dependencies; it served the packaged UI/assets outside the checkout. The [integrated UI review](ui-review.json) exercises the actual browser controller and DOM renderer against the real approved active corpus, BGE/hybrid retrieval, and the offline API. It covers eight flows:

- Full-corpus and named-document cited answers.
- A two-document comparison with a physical PDF citation.
- A partial comparison with one source's evidence gap.
- Not-covered, clarification, medical-refusal and nutrient-capability states.

The rendered claim text equals the API's validated claim text in every supported case. Native Chrome inspection separately verified layout and real click/submit behavior; jsdom is a test DOM and is not used as evidence of browser layout. This verification used **zero Groq calls**. The earlier [authenticated Groq review](groq-live-review.json) remains the evidence for the real hosted transport, with two successful calls totaling 876 tokens.

The 48 deterministic UI tests additionally cover all service-error states, unsafe markup/URLs, cross-document citation rejection, missing/mismatched responses, error bodies containing answer fields, broadened explicit selections, cancellation, duplicate submission, stale-response suppression, local timeout, network failure, availability changes, source limits/filtering, usage failure, unavailable UI configuration, diagnostic tab keyboard focus, lazy loading, offline usage, and separation of diagnostic metadata from answers. They use clearly labeled synthetic transport data for fault cases and do not exhaust the live account.

Node 18 or later is needed only to run development checks. Dependencies are pinned in `web/package-lock.json`; runtime assets have no third-party JavaScript dependency.

```bash
npm ci --prefix web
npm test --prefix web
npm run format:check --prefix web
.venv/bin/pytest tests/integration/test_api.py -q
.venv/bin/ruff check src tests

# Reproduce the real-corpus DOM review without hosted calls:
.venv/bin/dietary-rag serve --offline --port 8001
# In another terminal:
node web/inspect-ui.mjs http://127.0.0.1:8001
```

This is a local browser prototype. There is no authentication, ingestion/upload flow, persistent user memory or release-level semantic evaluation. Phase 7 evaluation and operational release tasks remain outstanding.

The diagnostics refinement passed 48 UI tests and 23 API integration tests, with packaged diagnostics assets verified in a rebuilt wheel. A real-corpus controller check verified readiness, 16 available/52 unavailable references, and the configured usage ledger without issuing chat requests or hosted calls.
