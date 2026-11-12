# Phase 7 evaluation and coverage (P7.1–P7.3)

P7.1 is implemented. P7.2 has completed the user-authorized Human–AI assisted learning-project review. Automated evaluation passes all 71 cases. Independent human review is not claimed.
P7.3 meets the indexed coverage quota. P7.4–P7.6 are implemented in the [operations runbook](operations-runbook.md); the learning-scope P7.7 gate passes. See [release decision](release-review.json) and [publication proof](learning-release-publication.json).

Run from the repository root with the existing ingestion/embedding environment,
reviewed local tokenizer/model, and validated active corpus:

```sh
python scripts/evaluate_release.py --output data/staging/evaluation/results.json
```

The command opens one validated immutable active pin, checks the frozen chunk
snapshot and expected evidence IDs, uses offline extractive generation, and writes
results plus a separate `.human-review.json` worksheet. It never reads `.env`,
calls Groq, downloads models, changes source approvals or promotes a snapshot.
Exit 0 means automated gates passed; exit 1 means scored findings fail the gates.
Missing/incompatible artifacts fail explicitly. Exit 0 does not mean release approval.
Preserve previous report files before rerunning; the output paths are overwritten.

The 71 cases in `config/evaluation-cases.json` reuse 31 answer regressions, add 32
questions from the reviewed retrieval set and five additional policy boundaries, plus three full-corpus/official/named-source cases.
Each records expected response state, required conditions, forbidden claims and
canonical evidence where applicable. The questions and acceptance targets are
recorded before scoring. The report binds the dataset bytes to a SHA-256 and
records the active release/snapshot. This is a project-authored diagnostic, not an
independent held-out answer benchmark. Fault injection tests numeric rejection,
bounded repair, timeout and provider outage; it is not live-provider reliability testing.

Acceptance targets are 100% structural/policy checks, at least 95% condition
completeness, at least 95% human-reviewed grounding, and zero human-reviewed
critical errors. Structural checks inspect exact support quotes, canonical chunk
ownership, per-document citations, physical PDF pages, selected search scope,
expected state and generation attempts. Results break down by topic, HTML/PDF
and prose/list/table evidence. Substring checks are diagnostic and do not establish
semantic entailment. The existing native-PDF, OCR/quarantine and exclusion review
artifacts remain prerequisites; the release suite does not pretend scanned fixture
content is consumer guidance or count it toward coverage.

## Retained results and error analysis

[Automated report](evaluation-review.json): **71/71 pass**, with 100% required-condition
completeness and passing structural/policy checks. The original 46/71 run and its
worksheet are retained in [baseline history](evaluation-history/baseline-46-of-71/evaluation-review.json).
The same frozen questions, required phrases, evidence IDs and acceptance targets
were used for both runs; no expectations were relabeled or thresholds lowered.

The 25 failures were fixed through shared runtime behavior:

- Recognize ordinary consumer topics and narrow pronoun follow-ups in multi-part
  questions, while keeping unknown/clinical follow-ups unresolved and deterministic
  medical/weight/bypass/lookup gates ahead of classification.
- Assess microwave instructions, thermometer use, outages, thawing, produce choices,
  sodium tips, hydration and age-qualified dairy/caffeine guidance using complete
  retrieved units. Add mayonnaise/deli-salad/juice/dough categories and distinguish
  prepared deli salad from raw/shell egg storage.
- Distinguish label comparison and dual-column literacy from serving-size advice.
  A narrow post-generation literacy exception handles source explanations that the
  lookup detector previously misread; numeric nutrient values and targets remain
  withheld. Sodium-removal instructions no longer become nutrient lookups merely
  because a food name and the word sodium occur together.

No model knowledge, altered source content, wider fallback search or corpus
promotion was used. Complete quotes, qualifiers, table notes, source separation,
canonical IDs and PDF pages still pass through the existing validators. Nearby
negative tests withhold incomplete cooking/outage/cleaning conditions, blank table
values, wrong-food substitutions, clinical/unknown follow-ups and prohibited claims
embedded in label explanations. This remains a project-authored diagnostic, not
independent semantic acceptance or proof of Groq quality.

[Completed worksheet](evaluation-review.human-review.json) contains case-level AI
verdicts/notes under the user's explicit learning-project authorization. Review scores:
71/71 grounded, 71/71 condition-complete, 71/71 policy-safe, zero critical errors.
The adult/child group lists are complete and the contradictory seasonal-produce
sodium gap is resolved; see [fix verification](completeness-fix-review.json). The original score thresholds are unchanged.
See the [reviewer-requirement revision](review-gate-revision.md) for scope, evidence,
limitations and the explicit `--learning-project` gate. This is AI-assisted acceptance
for a learning release, not independent human case-by-case sign-off.

## Final indexed eligibility and coverage

The validated active index contributes 269 reviewed chunks from **16 eligible
unique consumer families**, including **six approved official US PDFs**:
DGA 2025–2030, HHS adults/older adults, HHS kids/teens, Minnesota cross-contamination,
Minnesota safe grilling and NHLBI sodium tips. The report derives counts from
actual indexed chunk owners intersected with current registry eligibility, records
excluded sections, and lists missing/unexpected indexed documents. No unexpected
or missing eligible documents were found. Pages, alternate formats, reference
assets and OCR fixtures do not inflate family counts. No source expansion or
requirements revision is needed to meet 15-family/5-PDF quotas.

Compatibility, review receipts and eligibility are enforced when opening the
active pin; this audit does not independently re-extract every exclusion. Preserve
[normalization review](normalization-review.json), [chunking review](chunking-review.json),
[PDF review](pdf-extraction-review.json) and [OCR review](ocr-extraction-review.json)
as supporting evidence. Registry-recorded subject gaps remain visible in the
report. Meeting corpus quotas does not imply complete coverage of consumer questions.

Validation: 672 Python tests passed in clean hash-locked wheel reproduction, including the original evaluation fault/coverage tests and 16 added policy/condition regressions. Offline active-corpus API checks passed. Ruff lint and formatting passed for source, tests and the evaluation runner. Whole-repository checks also report existing registry-script and older Markdown formatting issues; those files were not reformatted in this change.
