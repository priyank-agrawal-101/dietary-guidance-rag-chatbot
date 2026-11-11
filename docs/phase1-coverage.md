# Coverage expansion and audit: P1.7

P1.7 is complete for the [Phase 1 acquisition milestone](implementation-plan.md).
The registry now contains **68 candidate URLs**, preserves all **20 original IDs**, and
selects **16 eligible US consumer guidance families**: **10 HTML assets and 6 PDFs**.
All 16 selected assets were imported with verified original acquisition receipts, and
their exact stored bytes were checked against the **current registry snapshot**.

There are **12 verified official US PDFs** in the registry, **9 approved with exclusions**.
Six are default consumer assets and have matching document versions in the provenance
store. The other PDFs remain separately labeled comparison/reference assets. Pages,
alternate formats and references do not inflate the default family count.

In everyday terms, P1.7 checks whether our library has enough useful, trustworthy books,
finds missing subjects, and avoids counting two editions of one book twice. It also makes
sure the books actually exist in storage. It does not check whether the chatbot understands
them yet; extraction and retrieval quality are later phases.

## Repeatable audit

```sh
source .venv/bin/activate
dietary-rag audit-coverage
dietary-rag audit-coverage --verify-acquisitions \
  --report data/staging/acquisition-reports/coverage-audit.json
python scripts/validate_source_registry.py --check-cache
```

The first command computes coverage from sources that **pass every consumer eligibility
filter**, rather than trusting the summary's proposed-default count. It performs no
downloads or storage writes. The second opens the existing SQLite database in read-only
mode, checks current-snapshot versions and recomputes artifact hashes/byte counts. Repeated
versions of one source count once. Old-snapshot versions, changed responses awaiting review,
missing files and corrupt artifacts do not count as matching acquisitions.

`--report` explicitly saves a JSON summary. A missing acquisition database produces exit
code 2 and is not created. Exit code 1 indicates a family/PDF shortfall or, when acquisition
verification is requested, missing or invalid consumer acquisitions. Exit code 0 means the
requested acquisition coverage checks passed; `published_corpus_ready` remains false.

The committed [coverage-audit.json](coverage-audit.json) records the verified milestone
result, selected scopes, alternate families, fingerprints and original retrieval times.
Regenerate it after registry changes. A clean checkout can run the metadata-only audit;
storage verification requires acquiring/importing the selected files first.

## New consumer coverage and recovered sources

| Selected source | Verified content/date | Selection reason and boundaries |
| --- | --- | --- |
| [CDC Preventing Food Poisoning](https://www.cdc.gov/food-safety/prevention/index.html) | HTTP 200; visible date Nov. 24, 2025 | Recovers the original `cdc_food_safety` ID. Federal clean/separate/cook/chill overview and recalled-food handling; exclude symptoms, treatment and risk statistics. |
| [CDC Safer Food Choices for Pregnant Women](https://www.cdc.gov/food-safety/foods/pregnant-people.html) | HTTP 200; visible date Jan. 31, 2025 | Recovers the existing pregnancy ID. Consumer food-choice/handling table; retain the pregnancy audience and row-specific conditions. No personalized prenatal diet or mercury serving target. |
| [FDA Selecting and Serving Produce Safely](https://www.fda.gov/food/buy-store-serve-safe-food/selecting-and-serving-produce-safely) | HTTP 200; content current as of March 5, 2024 | Recovers the existing produce ID. Buying, refrigeration, washing, separation and pre-washed produce; omit clinical/reporting sections. |
| [FDA Selecting and Serving Fresh and Frozen Seafood Safely](https://www.fda.gov/food/buy-store-serve-safe-food/selecting-and-serving-fresh-and-frozen-seafood-safely) | HTTP 200; content current as of March 5, 2024 | Recovers the existing seafood ID. Buying, freezing, thawing, thermometer-based cooking and serving; exclude prenatal fish-serving targets and contradictory visual-doneness wording. |
| [FDA How to Understand and Use the Nutrition Facts Label](https://www.fda.gov/food/nutrition-facts-label/how-understand-and-use-nutrition-facts-label) | HTTP 200; content current as of March 5, 2024 | New verified relocated URL. Serving-size meaning, %DV interpretation and label formats; omit calorie calculators, dietary-goal tables, treatment claims and individual-food nutrient examples. |
| [CDC Keep Food Safe After a Disaster or Emergency](https://www.cdc.gov/food-safety/foods/keep-food-safe-after-emergency.html) | HTTP 200; visible date Nov. 24, 2025 | New federal emergency coverage: refrigerator/freezer limits, temperatures, disposal/refreezing and flood-food disposal. Bleach recipes and commercial-can reconditioning await a separate process review. |

Every selected source was publicly fetched, opened and read for its relevant sections,
audience, date labels and exclusions. CDC/FDA document text was reviewed alongside the
agencies' actual reuse policies. The earlier failed receipts remain in the original review
directory and immutable registry history; a successful recheck does not erase them.

## Additional official PDFs and alternate editions

| Direct PDF | Issuer / physical pages | Text and layout | Decision |
| --- | --- | --- | --- |
| [Safe Food Handling: What You Need to Know](https://www.fda.gov/media/91319/download) | FDA; 2 pages; printed March 2017 | Selectable text on both pages; two-column colored panels, icons, a temperature table with wrapped rest-time cells on page 2. All pages rendered and inspected. | Approved with exclusions as an older format/layout comparison. Its [HTML](https://www.fda.gov/food/buy-store-serve-safe-food/safe-food-handling) is dated March 5, 2024. Both overlap the selected CDC prevention overview and are disabled as extra default sources. |
| [Raw Produce: Selecting and Serving it Safely](https://www.fda.gov/media/77178/download) | FDA; 4 pages; printed July 2023 | Selectable text on every page; columns, photos and callouts. Pages 1–2 contain produce guidance, page 3 illness/reporting material, page 4 a general handling infographic. All pages rendered and inspected. | Approved comparison asset, with pages 1–2 eligible and pages 3–4 excluded. The newer 2024 produce HTML remains the default. These are one family, not two independent sources. |

The PDF URLs came from links on the inspected FDA pages, not guessed media IDs. Each
returned an actual PDF signature, opened successfully with strict parser checks, and has
a SHA-256/byte-count receipt and a separate page/layout inspection record. The dates above
are printed issue dates; HTML update dates do not redate the PDFs. No scanned main-text
pages were found. These new FDA comparison PDFs are present in the review cache; they do
not contribute to the six default PDF document versions verified by the audit.

Existing Minnesota alternate-format comparisons retain one default per family:

| Family | Comparison and resulting selection |
| --- | --- |
| Cross-contamination | HTML's April 2007 revision and July 2026 page update remain separate from the PDF's April 2006 creation/2025 modification labels. Both cover shopping, refrigeration, preparation and serving; retain the PDF default and the HTML as comparison, with no independent corroboration count. |
| Safe grilling | HTML's August 2011 revision and January 2025 page update remain separate from the PDF's April 2007 creation/2025 modification labels. Temperature/holding/marinade context was compared; retain the PDF default. Whole-cut 145°F rows lacking the federal three-minute rest qualification and color-only safety claims are explicitly excluded. |
| Cold-storage chart | HTML's April 2007 revision/September 2026 page update and PDF's July 21, 2006 footer are preserved. Reviewed representative storage rows match, including shell eggs, raw meat, cooked meat and deli salads. Keep the HTML default and the PDF as a dated comparison; neither is silently presented as a newly published federal chart. |

The final 2025–2030 Dietary Guidelines and the scientific advisory report remain different
document roles, not interchangeable editions. The report, DASH treatment guide, old
heart-health medical guide and WHO references remain outside default consumer retrieval.
NHLBI's sodium PDF still permits only general shopping/seasoning tips on physical page 1.

## Sources deliberately withheld and unsuccessful locators

| Source/probe | Observation | Result |
| --- | --- | --- |
| [FDA Are You Storing Food Safely?](https://www.fda.gov/consumers/consumer-updates/are-you-storing-food-safely) | HTTP 200; January 18, 2023 update. Includes a four-hour above-40°F allowance and a 45°F passage that need reconciliation with the selected CDC emergency conditions. | Existing ID retained as `reference_only`; no default ingestion. Do not blend thresholds. |
| [FDA Safe Eats pregnancy page](https://www.fda.gov/food/people-risk-foodborne-illness/safe-eats-food-safety-moms-be) | HTTP 200; September 27, 2018. Mainly a directory of food-category subpages. | New `discovery_only` record; CDC's actual pregnancy table selected. Subpages were not blindly approved. |
| [Washington Power Outages](https://doh.wa.gov/emergencies/be-prepared-be-safe/severe-weather-and-natural-disasters/power-outages) | HTTP 200; revised March 2008, “reviewed annually” without a dated annual review. Broad utility/medical advice and subjective food-feels-cold wording. | Retained as a US state `reference_only` record. Current thermometer-based CDC food-emergency guidance selected. |
| Washington legacy `/Emergencies/EmergencyPreparednessandResponse/PowerOutages/FoodSafety` | HTTP 200 ultimately supplied a generic “Be Prepared, Be Safe” hub. | Reviewed locator only; does not count as food guidance. |
| Washington guessed `/you-and-your-family/food-safety/food-safety-during-power-outage` | HTTP 404. | Failed locator retained in review evidence, not added as approved content. |
| [FoodSafety.gov storage chart](https://www.foodsafety.gov/food-safety-charts/cold-food-storage-charts), [emergency chart](https://www.foodsafety.gov/food-safety-charts/food-safety-during-power-outage), [USDA leftovers](https://www.fsis.usda.gov/food-safety/safe-food-handling-and-preparation/food-safety-basics/leftovers-and-food-safety) | Bounded rechecks still returned HTTP 403. | Existing IDs retained `pending_access`; no content, date or rights approval invented. |
| FDA label `/food/food-labeling-nutrition/how-understand-and-use-nutrition-facts-label` and label-hub locator | HTTP 404; actual working label URL verified separately. | Failed locators retained in review evidence, not extra families. |
| FDA guessed policy `/about-fda/contact-fda/website-policies` | HTTP 404; actual footer-linked policy verified. | Policy evidence corrected; policy pages are not food knowledge sources. |

The Washington food-safety hub and Minnesota food-safety/emergency directories were used
to discover and assess links. Their directories were not added as answer evidence. Public
access alone does not establish relevance, current recommendations or reuse permission.

## Rights and scope

[FDA's actual website policy](https://www.fda.gov/about-fda/about-website/website-policies)
permits reuse unless otherwise noted and recommends recording the copy date, linking the
original and checking updates. [CDC's agency-materials policy](https://www.cdc.gov/other/agencymaterials.html)
requires agency attribution, a prominent non-endorsement disclaimer, preservation of
substantive content and notice that originals are available free on its website. The
registry records these obligations. Images, logos, third-party media and clinical content
remain excluded; block extraction must preserve recommendations and their conditions.

Existing HHS/NIH/Minnesota rights decisions remain in force. State publications are not
automatically federal public-domain works. Editorial/institutional unresolved rights and
Cleveland Clinic's recorded usage restrictions remain unchanged. No external messaging or
permission requests were required to complete the authorized local review and intake.

## Coverage result and practical limits

| Acquisition check | Verified result |
| --- | --- |
| Preserved original IDs | 20 of 20 |
| Eligible consumer assets / unique families | 16 / 16 |
| Eligible HTML / PDF assets | 10 / 6 |
| Verified / approved official US PDFs in registry | 12 / 9 |
| Current-snapshot consumer versions with matching stored bytes | 16 |
| Current-snapshot approved default official PDF acquisitions | 6; minimum 5 met |
| Family shortfall against minimum 15 | 0 |
| Real WHO attempted-PDF HTML response | Rejected as `format_mismatch`; remains disabled |
| Published chunks/index/chatbot | Not implemented |

Sixteen guidance families are **not sixteen independent authorities or sixteen nonoverlapping
topics**. The selection combines general federal prevention with focused thermometer,
grilling, storage, life-stage, produce, seafood, labeling and pregnancy guidance. Shared
recommendations must retain their source attribution and conditions instead of being
treated as multiple independent confirmations.

Federal emergency, pregnancy and seafood gaps now have verified consumer sources. The
FoodSafety.gov cold-storage chart and USDA leftovers page remain inaccessible; Minnesota
storage/leftovers material remains separately dated and attributed. MyPlate and NIA locators
remain unverified. Food allergy, infant feeding, home canning and regional shellfish
advisories are not claimed as comprehensively covered.

Original review receipts remain in `data/staging/source_review/2026-11-11/`; this review's
new receipts/PDF inspections are under its `p1-7/` subdirectory. Selected imports are
recorded in `data/staging/acquisition-reports/p1-7-consumer-imports.json`, the provenance
database, and the immutable raw store. These runtime files are ignored by Git. The committed
registry, this review and the audit summary preserve the review decisions and fingerprints.

The full suite passes **131 tests** on Python 3.13, including coverage shortfalls despite
proposed defaults, exclusion of conflicting/medical references, actual artifact corruption,
current-snapshot filtering, and read-only audit behavior. Registry/PDF cache validation,
lint, formatting, dependency consistency and wheel build also pass.

Proceed to Phase 2 extraction. Section exclusions, PDF reading order, tables, page identity,
OCR fixtures and chunk quality must be reviewed before publishing searchable evidence.
