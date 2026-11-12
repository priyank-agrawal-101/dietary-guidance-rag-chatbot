"""Only canonical snapshot metadata and reviewed original locations author citations."""

from urllib.parse import quote, urlsplit, urlunsplit

from dietary_rag.answering.contracts import AnswerValidationError
from dietary_rag.api.contracts import ChatResponse, Citation, Claim, DocumentAnswer
from dietary_rag.corpus.identity import stable_id


def citation_for(claim, chunk_id, prepared):
    resolved = prepared.evidence_request.resolve_evidence(chunk_id)
    chunk, doc = resolved["chunk"], resolved["build"].document
    pages = tuple(
        sorted(
            {loc.physical_page for loc in chunk.source_locations if loc.physical_page is not None}
        )
    )
    labels = tuple(
        dict.fromkeys(
            loc.printed_page_label for loc in chunk.source_locations if loc.printed_page_label
        )
    )
    url = urlsplit(str(doc.resolved_url or doc.source_url))
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
        raise AnswerValidationError("citation_url")
    if doc.format == "pdf":
        if not pages or any(p > doc.page_count for p in pages):
            raise AnswerValidationError("citation_page")
        fragment = f"page={pages[0]}"
    else:
        anchors = tuple(
            dict.fromkeys(loc.source_anchor for loc in chunk.source_locations if loc.source_anchor)
        )
        fragment = quote(anchors[0], safe="-._~") if anchors else url.fragment
    dates = doc.dates
    date_label = "; ".join(
        dict.fromkeys((*dates.source_stated_dates, *((dates.edition,) if dates.edition else ())))
    ) or (f"{dates.publication_year} publication" if dates.publication_year else None)
    quotes = tuple(s.support_quote for s in claim.evidence if s.chunk_id == chunk_id)
    payload = dict(
        document_id=doc.document_id,
        document_name=doc.document_name,
        publisher=doc.publisher,
        source_tier=doc.source_tier,
        jurisdiction=doc.jurisdiction,
        publication_year=dates.publication_year,
        date_label=date_label,
        format=doc.format,
        physical_pages=pages,
        page_urls=tuple(
            urlunsplit((url.scheme, url.netloc, url.path, url.query, f"page={p}")) for p in pages
        ),
        printed_page_labels=labels,
        source_url=urlunsplit((url.scheme, url.netloc, url.path, url.query, fragment)),
        section_heading=" > ".join(chunk.heading_path) or doc.document_name,
        chunk_ids=(chunk_id,),
        support_quotes=quotes,
    )
    return Citation(citation_id=stable_id("citation", payload), **payload)


def render_response(prepared, assessment, validated, unanswered):
    groups, citations = {}, {}
    for doc, claim, _units in validated:
        ids = []
        for chunk_id in dict.fromkeys(s.chunk_id for s in claim.evidence):
            citation = citation_for(claim, chunk_id, prepared)
            citations[citation.citation_id] = citation
            ids.append(citation.citation_id)
        groups.setdefault(doc, []).append(Claim(text=claim.claim_text, citation_ids=tuple(ids)))
    return ChatResponse(
        request_id=prepared.request_id,
        status="answered",
        corpus_version=assessment.corpus_snapshot_id,
        document_answers=tuple(
            DocumentAnswer(document_id=d, claims=tuple(c)) for d, c in groups.items()
        ),
        citations=tuple(citations.values()),
        searched_documents=prepared.search_result.audit.searched_document_ids,
        unanswered_aspects=tuple(unanswered),
    )


def render_text(response):
    """Plain text only; every factual claim carries its complete adjacent citation."""
    if not isinstance(response, ChatResponse):
        return f"{response.code}: {response.message}"
    if response.status != "answered":
        return response.message
    citations = {c.citation_id: c for c in response.citations}
    lines = []
    for answer in response.document_answers:
        lines.append(citations[answer.claims[0].citation_ids[0]].document_name)
        for claim in answer.claims:
            references = []
            for identifier in claim.citation_ids:
                c = citations[identifier]
                location = c.section_heading
                if c.physical_pages:
                    location += "; physical pages " + ", ".join(map(str, c.physical_pages))
                if c.printed_page_labels:
                    location += "; printed labels " + ", ".join(c.printed_page_labels)
                references.append(
                    f"[{c.document_name}; {c.publisher}; {c.jurisdiction}; {c.source_tier}; {c.date_label or 'source date unknown'}; {location}; {', '.join(map(str, c.page_urls)) if c.page_urls else c.source_url}]"
                )
            lines.append(f"{claim.text} {' '.join(references)}")
    lines.extend(response.unanswered_aspects)
    return "\n\n".join(lines)
