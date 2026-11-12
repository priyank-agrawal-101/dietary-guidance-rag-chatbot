# Learning-project Human–AI assisted review gate

On November 12, 2026, the user explicitly authorized counting this review as a
Human–AI assisted gate because the repository is a learning project, not a live
service for actual users. This revises the reviewer requirement for the learning
release only. It does not change the frozen cases, numeric acceptance targets,
code-enforced medical/calorie/weight/lookup boundaries, source eligibility or
citation validators.

The AI coding agent inspected all 71 complete responses against their question,
expected state, support quotes, reviewed canonical evidence and original sources.
Original HTML table values were inspected, and all nine cited physical PDF pages
were rendered and visually checked. The human contribution is the user's explicit
scope/acceptance authorization; no independent human case-by-case review is claimed.

[Completed worksheet](evaluation-review.human-review.json) records
`review_type: human_ai_assisted_learning`, `acceptance_scope: learning_project_only`,
`independent_human_review: false`, the result SHA-256, authorizing user instruction,
review method and case-level verdicts/notes. The prior pending worksheet is retained
in `evaluation-history/pre-assisted-review/`.

Initial learning-v0.1.0 review: 71/71 grounded, 68/71 condition-complete (95.77%),
71/71 policy-safe, and zero critical errors. These three initial findings are now resolved:

- `adults-pdf`: introductory food-group sentence ends before listing the groups.
- `children-pdf`: same incomplete introduction in the correctly selected child guide.
- `expanded-adult-seasonal-produce`: sodium gap message contradicts the supported
  low/no-added-sugar-or-sodium advice already supplied.

Additional case notes disclose irrelevant inferred gaps, unrelated source
contributions in full-corpus answers, duplicate table labels, trailing headings
and ambiguous original outage wording. These remain visible learning backlog
items; the review does not relabel them as flawless answers.

The learning review meets the original ≥95% grounding/condition, 100% policy and
zero-critical-error thresholds. Acceptance applies only to the exact report hash.
It is not independent population evaluation or live Groq quality verification.

Use the explicit learning switch:

```sh
.venv/bin/python scripts/check_release.py --learning-project
.venv/bin/python scripts/check_release.py --learning-project \
  --publish-dir data/releases/learning-v0.1.1
```

Without `--learning-project`, the checker retains the independent-human gate and
rejects this AI-assisted worksheet, even though its status is completed. The learning
switch also requires the worksheet's matching scope, AI reviewer type, explicit
user authorization and all original score thresholds. A fourth condition failure
or any critical error fails the learning gate. Release reports/bundles disclose
`release_scope: learning_project_only` and `independent_human_review: false`.

The follow-up review is bound to the current results hash and scores 71/71 on
grounding, completeness and policy safety, with zero critical errors. All 68 other
response bodies/citations are unchanged. See [fix verification](completeness-fix-review.json)
and the retained initial evidence in `evaluation-history/learning-v0.1.0/`.

Food-group answers retrieve bounded same-page reviewed cards linked to the original
introduction. The answer uses exact group-label quotes and comma formatting. Ranked
search limits remain the initial-hit budget; linked cards share the configured
maximum candidate budget and appear in `search_audit.context_chunks`. They remain
inside the original searched document/version and pass canonical citation validation.
Missing group context yields a gap rather than an incomplete introduction.
