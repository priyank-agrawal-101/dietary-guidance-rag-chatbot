# Problem Statement: Dietary Guidance RAG Chatbot

This document expands the project brief in [problemStatement.txt](problemStatement.txt) and records the current project scope: **both HTML guidance pages and PDF documents for a U.S. audience**, prioritizing official US government sources and retaining relevant institutional/editorial candidates with access and rights decisions. The original text remains a historical brief; this Markdown specification incorporates the subsequent source-count, audience, publisher, and HTML/PDF ingestion updates. Implementation suggestions and examples are identified separately. This is a specification for the prototype, not an implemented corpus or service.

## 1. Background and Context

Health authorities publish detailed guidance on food, nutrition, and food safety. These documents explain topics such as healthy eating patterns, food handling, storage, and preparation. They are often long PDFs containing prose, tables, and numbered recommendations, which makes finding a specific answer difficult for an everyday reader.

The underlying guidance already exists. The problem is making relevant passages easy to find and understand while preserving their authority, context, and source attribution. A chatbot can provide a conversational interface, but its answers must remain grounded in the selected guidance documents.

Retrieval-Augmented Generation (RAG) addresses this gap by retrieving relevant passages from a document collection and using those passages as the evidence for an answer. In this project, retrieval is the foundation of the chatbot's answers: the assistant must not fill gaps using unsupported model knowledge.

In the final project, this prototype will become the service behind questions such as:

- "Is this a reasonable way to eat?"
- "How long can I keep this in the fridge?"

These describe the intended use of the service, but any response remains subject to the available evidence and the exclusions below. The service is not a source of medical advice or personalized calorie and weight targets.

## 2. Core Problem

Build a prototype chatbot that answers U.S. users’ questions about food, nutrition, and food safety using only retrieved evidence from an approved collection of public HTML guidance pages and PDF documents. U.S. government sources provide official guidance; academic, clinical, and editorial sources are attributed according to their actual source type.

The prototype must solve three connected problems:

1. **Find relevant guidance:** Locate useful passages across a small collection of authoritative documents, including within a specifically named document.
2. **Explain the evidence:** Produce understandable answers in which every factual claim is supported by retrieved text and carries a citation.
3. **Respect evidence and scope limits:** Say when the searched guidance does not contain an answer, and separately decline requests that are outside the project's permitted scope.

Success means that a reader can trace an answer back to its source and understand when the chatbot cannot answer.

## 3. Objectives

- Make authoritative written guidance accessible through natural-language questions.
- Build a mixed HTML/PDF corpus, initially targeting 15 to 20 distinct U.S.-relevant consumer guidance families. The registry may exceed that target to track alternate formats, specialized references and unavailable candidates. Include at least 5 verified official US PDFs in the approved acquisition list.
- Cover eating patterns, food groups, grains, fiber, fats, food labels, contamination prevention, cooking, chilling, storage, and leftovers.
- Preserve the distinction between official guidance and secondary explanations.
- Preserve document provenance and section context during ingestion and chunking.
- Support vector retrieval across the corpus and retrieval restricted to one named document.
- Generate answers exclusively from retrieved chunks, with citations attached to every claim.
- Keep the contributions of different documents separate in cross-document answers.
- Enforce distinct behavior for missing evidence and prohibited requests.
- Explain the chunking strategy and its trade-offs in the README.

## 4. Scope and Boundaries

### 4.1 In Scope

- General questions about food, nutrition, and household food safety for a U.S. audience that the selected pages address.
- Retrieval from approved U.S.-relevant HTML/PDF sources, with document-specific access, rights and audience checks.
- U.S. food-safety conditions and U.S. Nutrition Facts labeling context, with units and applicability preserved.
- General food-selection and food-group guidance; personalized medical, calorie, and weight advice remains excluded.
- Answers based on one document or multiple documents, with source attribution preserved.
- Questions asking what a particular document says about a topic.
- Explicit acknowledgment when the retrieved evidence does not answer a question.

### 4.2 Out of Scope by Design

The assistant must decline requests for:

- Medical advice.
- Calorie targets.
- Weight targets.
- Advice about what a person should weigh.

For these requests, the assistant must direct the user to a qualified professional. These restrictions must be enforced in code; a prompt instruction alone does not satisfy the brief.

### 4.3 Data Reserved for Milestone 3

Nutrient values for individual foods belong to a separate structured-data capability in Milestone 3. They are not part of this guidance-document RAG prototype.

For example, asking for the amount of a nutrient in a particular food is different from asking what an official guidance document recommends about eating patterns. The former belongs to the structured database; the latter may be answered here if supported by retrieved guidance.

Sources whose relevant data already has a clean API are excluded from this corpus. This project focuses on retrieving written prose, not reproducing a structured-data lookup service.

## 5. Functional Requirements

### 5.1 Corpus Collection and Provenance

Gather a mixture of **HTML guidance pages and PDF documents**. Prioritize USDA, FDA, CDC, HHS, NIH/NIDDK/NHLBI, FoodSafety.gov and relevant state public-health authorities. U.S. universities, academic medical centers and established health publishers remain potential references where relevance and reuse scope are established. Preserve the distinction between official guidance and secondary explanations.

The initial planning target is 15 to 20 **distinct consumer guidance families**, with no hard limit on registry URLs. Alternate HTML/PDF versions, discovery pages, specialized references and failed candidates must not inflate independent source coverage. At least **5 relevant, accessible official US PDFs** must be approved, ideally across authorities and topics. The P1.7 review verifies 12 official PDFs and approves 9 with exclusions; six default PDFs have matching stored acquisitions. This is acquisition approval and storage verification, not proof that extraction, chunking or indexing has been implemented.

For both formats, visit the actual source and review its accessibility, currency, audience, relevance, duplication and reuse notices. A public URL is not automatic permission for RAG ingestion. Retain relevant existing sources even when access or rights decisions disable ingestion. Optional international material may remain only as clearly identified, nonconflicting general reference; exclude it from default US consumer retrieval and never import non-US regulatory targets.

HTML extraction must preserve headings, lists, table headers, units, conditions and notes. PDF ingestion must verify the **direct file URL**, HTTP response, PDF signature and parser readability, record physical page count and selectable-text/scanned status, and inspect complex layouts. Implement native text extraction first and page-specific OCR where needed. Learning extraction, cleaning, normalization, chunking and indexing for both formats is a milestone objective. A download landing page is discovery metadata, not the PDF itself.

Separate general household guidance from specialized medical diets, scientific advisory reports, historical publications and food-service regulatory documents. The FDA Food Code, if acquired, is a regulatory reference rather than a household storage guide. CKD, diabetes and blood-pressure diet documents are not default general dietary recommendations. The final Dietary Guidelines for Americans and the advisory committee's scientific report have distinct roles and must not be conflated.

Screen broader articles at section/block level. Exclude disease-treatment advice, supplement treatment/dosing, calorie or weight targets, and individual-food nutrient lookup tables. Retain eligible general dietary guidance with its qualifications.

Store the following information for every document:

| Field | Purpose |
| --- | --- |
| Document name | Identifies the source in retrieval results and citations. |
| Publisher | Identifies the authority responsible for the guidance. |
| Publication year / date label / edition | Identifies the publication without turning an edition, update or retrieval date into an invented publication year. |
| Source URL | Lets the reader access the original document. |
| Retrieval date | Records when the document was obtained. |
| Source type | Distinguishes official government guidance, institutional references, and editorial references. |
| U.S. applicability | Records the intended audience and any state, age, food, or storage-condition limitations. |
| Author and reviewer | Records named authors, clinical reviewers, and review dates when present. |
| Inclusion/exclusion and rights notes | Records eligible sections, audience limits, copyright/permission findings, source failures, and metadata gaps. |
| Format and PDF inspection | Records HTML/PDF detection, direct PDF URL, page count, text/OCR classification, layouts and inspected pages. |
| Family and acquisition evidence | Records duplicate/alternate versions, HTTP result, exact retrieval time, content hash and review decision. |

**Implementation suggestions:** Maintain a corpus manifest, use a consistent date format, and record an edition or version when available. Do not invent missing publication details; make any metadata limitations visible.

#### 5.1.1 Reviewed Source Registry

Use [config/sources.yaml](../config/sources.yaml) as the source of truth and [sourceReview.md](sourceReview.md) for the detailed source-by-source assessment, public URLs, verified direct PDF links and selection reasons. The registry follows the architecture’s planned `config/sources.yaml` convention; do not maintain competing source lists in multiple documents.

The November 11, 2026 P1.7 review records **68 candidate URLs**, preserves **all 20 earlier IDs**, verifies **12 official US PDFs**, and approves **9 PDFs with recorded exclusions**. The default selection contains **16 eligible consumer families: 10 HTML assets and 6 PDFs**, all imported and byte-verified under the current registry snapshot. This meets the initial acquisition family/PDF target before block extraction and indexing; dated comparison PDFs and other references remain separate. See [coverage review](phase1-coverage.md) and [coverage audit](coverage-audit.json). Unavailable sources, unresolved reuse rights and duplicated formats remain visible and do not count toward released coverage.

The approved PDFs cover federal dietary guidance, adult/older-adult and child nutrition, general sodium-reduction tips, household cross-contamination, grilling and a dated storage chart. The scientific committee report and old NIH heart/DASH booklets remain separately labeled references. No actual FDA PDF or Food Code 2026 edition was verified during the review. HTTP 403/405, 404 and DNS failures are recorded as observed acquisition failures, not proof that all corresponding publications are absent.

Before indexing, implement and review section exclusions, PDF reading order and table fidelity. Keep original and resolved URLs, format-specific publication/revision/edition dates and exact retrieval times. A page’s update date must not make an older linked PDF appear current, and alternate HTML/PDF copies must not become independent corroborating authorities.

#### 5.1.2 Source Types and U.S. Authority Rules

| Source type | Meaning | Use in answers |
| --- | --- | --- |
| `official_guidance` | U.S. federal or state government guidance. | Foundation for applicable food-safety and regulatory recommendations; identify state-issued guidance accurately. |
| `institutional_reference` | U.S. university or academic medical-center reference material. | Supports general nutrition explanations and food choices, with its own attribution. |
| `editorial_reference` | Healthline or WebMD articles, subject to review and reuse clearance. | Use only approved eligible passages; public availability and medical-review labels do not establish RAG reuse permission. |
| `international_reference` | Optional authoritative international general reference. | Disabled in default US retrieval; only nonconflicting, clearly attributed general context after applicability and rights checks. |

Prioritize applicable government guidance for food-safety or regulatory claims when sufficient evidence is available. Source authority cannot compensate for an irrelevant passage. Do not describe Harvard, Cleveland Clinic, Healthline, or WebMD articles as government guidelines. A medical-review badge does not authorize personalized medical advice.

Keep each document's claims and citations separate. If retrieved sources differ, show their conditions and attribution instead of averaging recommendations or turning repeated secondary explanations into a new consensus claim. For a named-document query, respect that document scope and make its source type visible.

U.S. answers should use familiar U.S. units where appropriate, including °F for temperatures. Preserve original source values and conditions; any unit conversion must be deterministic and retain the source value. Exclude non-U.S. regulatory rules and recommendations that cannot be established as applicable to the U.S. question.

### 5.2 Chunking and Context Preservation

Convert documents into retrievable chunks while preserving enough context to interpret the guidance correctly.

Every chunk must carry:

- Document name.
- Publisher.
- Verified publication year or explicitly labeled source date/edition; show an unknown date honestly.
- Section heading.
- Source type and U.S. applicability.

For PDF-derived chunks, retain physical page index, printed page label when available, extraction method and original block locations. Preserve tables and their footnotes across page boundaries; do not chunk at an arbitrary page break.

The source URL must also remain accessible to the answer layer so it can generate the required citation. It may be attached to each chunk or resolved through the document's metadata.

Chunking must account for the structure of the source material. Fixed-size splitting can separate a recommendation from its explanation, divide a numbered list, or detach a table entry from its heading. These failures can make retrieved passages incomplete or misleading.

The README must explain the chosen chunking approach and what it costs. Relevant trade-offs include preserving complete sections versus retrieving focused passages, adding overlap versus duplicating text, and retaining table context versus increasing chunk size.

**Implemented design choice (P2C.1–P2C.3):** Structure-aware chunks retain complete recommendations, reviewed card boundaries, nested lists and labeled table rows. The chosen baseline is a 350-token preferred target with up to 50 tokens of complete-sentence prose overlap, using the pinned E5 tokenizer and a verified 512-token full-input limit without truncation. Comparison sources stay separate and quarantined OCR remains unavailable. See [architecture §9](architecture.md#9-chunking-strategy) and [chunking foundations/results](phase2-chunking.md). The original brief does not prescribe these parameters; they are the accepted implementation choice, pending broader dense retrieval evaluation.

### 5.3 Vector Retrieval

Build a vector index over the document chunks.

The retrieval layer must support both:

1. **Corpus-wide retrieval:** Search across all collected documents.
2. **Named-document retrieval:** Search only within the document selected or named by the user.

Retrieved results must retain the metadata needed to identify the source, preserve section context, show source type and U.S. applicability, and construct citations. Search only eligible U.S.-relevant chunks; support source-type restrictions when a user requests official guidance. When a user restricts a question to a document, the answer must respect that restriction.

**Implementation suggestions:** Use a stable document identifier for filtering and retain the scope of each search for refusal messages. The embedding model, vector store, result count, and evidence-sufficiency method are implementation choices.

### 5.4 Grounded Answer Generation and Citations

The answer layer must use only retrieved chunks as evidence for its factual claims. It must not invent recommendations, add unsupported facts, or substitute general model knowledge when relevant guidance is missing.

Every factual claim must carry a citation showing:

- Document name.
- Publisher.
- Verified publication year or explicitly labeled source date/edition; show an unknown date honestly.
- A link to the source.

Make the source type visible alongside citations so government recommendations and secondary explanations retain their identity.

Citations should be attached closely enough to the supported claims that the reader can tell which source supports which statement. A source list alone does not make that relationship clear.

Illustrative citation structure:

```text
[Claim supported by retrieved text.] ([Document name], [Publisher], [verified year/date label/edition], [section or PDF page] — [Source link])
```

The text inside brackets above is a placeholder, not actual guidance. PDF citations must include physical page references and link to the verified direct file, optionally using `#page=N`. Printed page labels supplement the physical index and are not assumed to match it. HTML citations retain section/anchor context where available. An edition such as 2025–2030 must be labeled as an edition, not an invented publication date.

### 5.5 Cross-Document Questions

Some topics may be covered by more than one authority. The brief uses cooking oil as an example of a subject where a nutrition institute and a food safety regulator may each have relevant guidance.

For such questions, the chatbot must answer **per document**, with separate citations. It must preserve each document's contribution instead of merging the sources into one claim about what "the guidelines say."

Illustrative answer structure:

```text
Document A:
[Answer supported by Document A's retrieved passages, with its citation.]

Document B:
[Answer supported by Document B's retrieved passages, with its citation.]
```

If sources address different aspects of a topic, those differences should remain visible. If retrieved passages contain differing recommendations, the answer should attribute them separately rather than invent a common recommendation.

### 5.6 Two Distinct Refusal Behaviors

#### A. Missing Evidence: Not in the Corpus

Use this response when a question is otherwise within scope but the retrieved chunks do not contain the answer.

The assistant must:

- Say that the searched guidance does not cover the question or does not provide the requested answer.
- Name what it searched, including the relevant documents or the selected document.
- Avoid supplying an unsupported answer.

Illustrative response:

> I could not find an answer in the guidance I searched: [document names]. Those retrieved passages do not cover this question.

This describes a limitation of the searched corpus. It does not imply that no authority anywhere has published an answer.

#### B. Prohibited Request: Out of Scope by Design

Use this response for medical advice, calorie targets, weight targets, or advice about what someone should weigh.

The assistant must:

- Decline the requested advice or target.
- Direct the user to a qualified professional.
- Enforce the restriction through code, even if related material exists in the corpus.

Illustrative response:

> I cannot provide medical advice or set calorie or weight targets. Please consult a qualified healthcare professional for advice appropriate to your circumstances.

The original brief refers to enforcing these restrictions "same as last week." It does not provide that earlier implementation; the explicit requirement here is code-level enforcement.

## 6. Suggested End-to-End Workflow

The following workflow organizes the required behavior without prescribing a technology stack:

1. **Collect sources:** Review eligible HTML/PDF documents, targeting 15–20 distinct consumer guidance families and at least 5 official PDFs; record access, rights and provenance.
2. **Extract content:** Parse HTML or extract PDF text/layout; apply page-specific OCR where needed and preserve headings, tables, page locations and exclusions.
3. **Clean and normalize:** Convert both formats into reviewed common content blocks without changing values, units, qualifiers or reading order.
4. **Create chunks:** Apply the chosen strategy and attach the required metadata.
5. **Build the index:** Embed the chunks and store them in a searchable vector index.
6. **Check request scope:** Apply code-enforced restrictions to prohibited requests.
7. **Retrieve evidence:** Search the full corpus or the named document, as requested.
8. **Assess support:** Determine whether the retrieved passages answer the question; relevance alone does not guarantee sufficient evidence.
9. **Respond:** Produce supported, cited claims, organize multiple sources separately, or issue the appropriate refusal.

The brief does not require a particular frontend, framework, model provider, embedding model, or vector database.

## 7. Illustrative User Scenarios

These scenarios illustrate expected behavior. They do not assert that the eventual corpus contains an answer to each example.

| User request | Expected behavior |
| --- | --- |
| "What does the guidance say about a balanced eating pattern?" | Retrieve relevant passages and provide cited claims if sufficient evidence exists. |
| "According to [document name], what guidance is given on food storage?" | Restrict retrieval to the named document and answer only from its retrieved evidence. |
| "What do these documents say about cooking oil?" | Present each relevant document's guidance separately, with its own citations. |
| "How long can I keep this food in the fridge?" | Answer only if the retrieved guidance supports the relevant food and storage conditions; otherwise identify the searched sources and the evidence gap. |
| "What should I eat to treat my medical condition?" | Decline medical advice and direct the user to a qualified professional. |
| "How many calories should I eat to reach a target weight?" | Decline calorie and weight targets through the code-enforced scope boundary. |
| "How much protein is in 100 grams of this food?" | Identify this as an individual-food nutrient lookup reserved for the structured-data capability in Milestone 3. |

## 8. Expected Deliverables

The requirements imply the following prototype deliverables:

- A reviewed acquisition registry and a mixed HTML/PDF corpus with document-level metadata, targeting 15–20 distinct consumer guidance families and at least 5 verified official US PDF sources.
- Format-specific HTML/PDF extraction, cleaning and normalization feeding a common chunking/indexing process, with source, section and PDF page metadata.
- A vector index supporting corpus-wide and named-document retrieval.
- An answer layer that uses retrieved evidence and cites every factual claim.
- Cross-document answer behavior that keeps source contributions separate.
- A missing-evidence response that names the searched guidance.
- Code-enforced exclusions for medical advice, calorie targets, weight targets, and advice about what someone should weigh.
- README documentation describing the chunking decision and its trade-offs.

## 9. Acceptance Criteria

The following checklist translates the brief into reviewable outcomes:

- [x] The acquisition registry includes both HTML and at least 5 verified relevant official US PDFs with direct URLs, issuer/title, page counts, text/OCR classification and layout notes. P1.7: 12 verified/9 approved official PDFs; 6 default PDF acquisitions byte-verified.
- [ ] The released consumer corpus targets at least 15 distinct U.S.-relevant guidance families; alternate formats, unavailable candidates and reference-only documents do not inflate the count. Registry expansion beyond 20 URLs is allowed.
- [ ] Extraction, cleaning, normalization, chunking and indexing are demonstrated for both HTML and PDF.
- [ ] OCR fallback is tested on a labeled scan fixture; native selectable PDFs are not falsely described as scans.
- [ ] Discovery pages are not mistaken for documents, and non-U.S. regulatory advice is excluded.
- [ ] Government, institutional, and editorial sources are labeled accurately in metadata and answers.
- [ ] Broader articles exclude medical treatment advice, calorie/weight targets, and individual-food nutrient lookup data.
- [ ] Coverage includes general eating patterns, food groups, labels, cooking, contamination prevention, chilling, storage, and leftovers.
- [ ] Unavailable, usage-blocked and rights-unresolved sources remain recorded but unindexed.
- [ ] Specialized medical, scientific, historical and food-service regulatory references remain separate from default consumer guidance.
- [ ] Duplicate HTML/PDF families are reconciled without counting both as independent evidence.
- [ ] Each document records its name, publisher, actual source date/edition or explicit date gap, original/resolved URL, format, rights decision and retrieval time.
- [ ] Every chunk carries the document name, publisher, source date/edition label, section heading and, for PDFs, physical page/location provenance.
- [ ] The README explains the chunking strategy and its costs or trade-offs.
- [ ] Vector retrieval works across the entire corpus.
- [ ] Retrieval can be restricted to one named document and respects that restriction.
- [ ] Answers contain only claims supported by retrieved chunks.
- [ ] Every factual claim includes the document name, publisher, verified date/edition label, source link and applicable section/PDF pages in its citation.
- [ ] Cross-document answers present each document's contribution separately with separate citations.
- [ ] When retrieved evidence is insufficient, the assistant says so and names what it searched.
- [ ] Requests for medical advice, calorie targets, weight targets, or advice about what someone should weigh are declined with a referral to a qualified professional.
- [ ] Those scope restrictions are enforced in code.
- [ ] Individual-food nutrient lookup data remains outside this prototype and is reserved for Milestone 3.

**Suggested validation:** Review representative questions for each supported behavior, including an answerable question, a named-document query, a multi-document query, an evidence gap, and prohibited requests. Inspect the retrieved passages alongside the answer to check both citation correctness and whether each claim is actually supported.
