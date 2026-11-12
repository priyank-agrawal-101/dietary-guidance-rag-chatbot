# Chat experience improvements

The development server now uses concise source-gap and scope replies. Raw source
IDs and per-source gap diagnostics remain available in a collapsed **Diagnostic
details** section on each response. Supporting excerpts and source links remain
available alongside cited answers.

- “How long can hard-cooked eggs be kept in the refrigerator?” recognizes the
  supplied preparation, including ordinary and Unicode hyphens, and returns the
  cited seven-day refrigerator row without requesting the same detail again.
- “What information you have about cooking oil.” retrieves the existing Dietary
  Guidelines cooking-fat passage. It does not invent smoke points, reuse rules,
  unsupported named-oil comparisons, nutrient quantities or medical claims.
- “Do you know about President of US?” is routed before retrieval as an unrelated
  topic, with a friendly explanation of supported food/nutrition questions.
- When searching the whole collection, a source that lacks an answer no longer
  creates an unanswered aspect if another source supports that aspect. Explicit
  source comparisons retain per-source gaps; actual unanswered aspects remain.

Source loading checks readiness before requesting the document list and retries
one brief busy response. Status checks have two worker slots separate from the
bounded chat capacity. A shared 30-second metadata cache coalesces validation;
active-pointer/release and snapshot-manifest checks precede cache reuse. Chat still
validates its pinned corpus for every request. Cold/expired status checks can still
wait for corpus access, and real capacity/timeout errors retain helpful retry text.

Verification: **687 Python tests**, **50 UI tests**, **71/71 frozen evaluation
cases**, lint and formatting pass. Four offline checks use the real active corpus;
both supplied egg/oil questions also succeeded through the restarted HTTP server
and configured Groq provider. The unrelated-question HTTP reply was verified.
See [recorded responses and checks](ux-review.json). Browser visual verification
was unavailable because the browser access host was unavailable; DOM integration
checks cover collapsed details, safe rendering and retry behavior.

Reload [the local chat](http://127.0.0.1:8000/) to load the updated interface. This
is a development update after the preserved `learning-v0.1.1` bundle; prior release
reports continue to describe that published version.

## Microwave-question follow-up

The exact suggested question “How do microwave wattage and cooking time affect
reheating leftovers safely?” now returns the complete CDC passage. General
“microwaved food” reheating evidence applies to the leftovers category for this
aspect; it does not supply storage durations or omit wattage, minimum/maximum
time or thermometer conditions. Both full-collection and selected-CDC checks pass,
as do 95 relevant tests and the unchanged 71-case evaluation. The restarted local
server also answered through Groq. See [verification](microwave-paraphrase-review.json).
