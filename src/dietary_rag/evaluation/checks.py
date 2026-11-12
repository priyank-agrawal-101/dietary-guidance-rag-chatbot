"""Independent output checks and current indexed-family accounting."""

from collections import Counter


def check_answer(case, report, chunks):
    response = report["response"]
    text = " ".join(c["text"] for d in response.get("document_answers", []) for c in d["claims"])
    checks = {
        "expected_state": response.get("status", response.get("code")) == case["expected_status"],
        "required_conditions": all(
            p.casefold() in text.casefold() for p in case["required_phrases"]
        ),
        "forbidden_claims": all(
            p.casefold() not in text.casefold() for p in case["forbidden_phrases"]
        ),
    }
    if "expected_generation_attempts" in case:
        checks["attempt_budget"] = (
            report["inspection"]["generation_attempts"] == case["expected_generation_attempts"]
        )
    if "expected_answer_documents" in case:
        checks["document_contributions"] = [
            d["document_id"] for d in response.get("document_answers", [])
        ] == case["expected_answer_documents"]
    selected = case["request"].get("scope", {}).get("document_ids")
    if response.get("status") == "answered":
        checks["selected_scope"] = not selected or set(response["searched_documents"]) == set(
            selected
        )
        citations = {c["citation_id"]: c for c in response["citations"]}
        valid = True
        for document in response["document_answers"]:
            for claim in document["claims"]:
                valid &= bool(claim["citation_ids"])
                for identifier in claim["citation_ids"]:
                    citation = citations.get(identifier)
                    if not citation or citation["document_id"] != document["document_id"]:
                        valid = False
                        continue
                    ids, quotes = citation["chunk_ids"], citation["support_quotes"]
                    valid &= bool(ids) and bool(quotes) and all(i in chunks for i in ids)
                    if not all(i in chunks for i in ids):
                        continue
                    valid &= all(chunks[i].document_id == document["document_id"] for i in ids)
                    valid &= all(any(q in chunks[i].text for i in ids) for q in quotes)
                    pages = sorted(
                        {
                            loc.physical_page
                            for i in ids
                            for loc in chunks[i].source_locations
                            if loc.physical_page is not None
                        }
                    )
                    valid &= list(citation["physical_pages"]) == pages
                    if citation["format"] == "pdf":
                        valid &= bool(pages) and len(citation["page_urls"]) == len(pages)
                        valid &= all(
                            url.endswith(f"#page={p}")
                            for p, url in zip(pages, citation["page_urls"], strict=False)
                        )
        checks["canonical_citations"] = bool(response["document_answers"]) and valid
    return checks


def audit_indexed_coverage(registry, chunks):
    eligible = {s.document_id: s for s in registry.consumer_sources()}
    counts = Counter(c.document_id for c in chunks.values())
    unexpected = sorted(set(counts) - set(eligible))
    sources = [eligible[i] for i in counts if i in eligible]
    families = {s.family_id for s in sources}
    pdfs = [
        s.document_id
        for s in sources
        if s.format == "pdf"
        and s.selection.approved_pdf_for_milestone
        and s.source_tier == "official_guidance"
        and (s.jurisdiction == "US" or s.jurisdiction.startswith("US-"))
    ]
    return {
        "eligible_indexed_family_count": len(families),
        "official_reviewed_pdf_ids": sorted(pdfs),
        "unexpected_indexed_documents": unexpected,
        "missing_eligible_documents": sorted(set(eligible) - set(counts)),
        "passes_quota_and_eligibility": len(families) >= 15 and len(pdfs) >= 5 and not unexpected,
        "documents": [
            {
                "document_id": s.document_id,
                "family_id": s.family_id,
                "format": s.format,
                "chunks": counts[s.document_id],
                "excluded_sections": s.selection.excluded_sections,
            }
            for s in sources
        ],
        "limitations": registry.review_summary.get("coverage_gaps", []),
    }
