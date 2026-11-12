"""Bounded retrieval of reviewed food-group cards linked to an introduction."""

import re
from dataclasses import replace

from dietary_rag.retrieval.contracts import EvidenceHit


def food_group_context(prepared, limit):
    result = prepared.search_result
    if not re.search(r"food groups|variety of healthy foods", result.audit.resolved_question, re.I):
        return prepared
    retriever = getattr(prepared.evidence_request, "retriever", None)
    if retriever is None:
        return prepared
    introductions = [
        hit for hit in result.hits if re.search(r"food groups:\s*$", hit.chunk.text, re.I)
    ]
    hits = list(result.hits)
    contexts = dict(result.audit.context_chunks)
    for intro in introductions:
        pages = {loc.physical_page for loc in intro.chunk.source_locations if loc.format == "pdf"}
        for chunk in sorted(retriever.chunks.values(), key=lambda c: c.sequence_index):
            if (
                chunk.document_version_id != intro.chunk.document_version_id
                or chunk.document_id != intro.chunk.document_id
                or not any(
                    re.fullmatch(r"page:\d+:food_group_\d+_\d+", k) for k in chunk.region_keys
                )
                or not {loc.physical_page for loc in chunk.source_locations} <= pages
                or chunk.chunk_id in {hit.chunk.chunk_id for hit in hits}
            ):
                continue
            if len(hits) >= limit:
                break
            resolved = prepared.evidence_request.retrieve_food_group_context(
                chunk.chunk_id, intro.chunk.chunk_id
            )
            if resolved["chunk"] != chunk or resolved["build"].document != intro.document:
                raise ValueError("linked canonical evidence changed within snapshot")
            hits.append(
                EvidenceHit(
                    chunk=chunk,
                    document=intro.document,
                    rank=len(hits) + 1,
                    score=0.0,
                    branch_ranks={},
                )
            )
            contexts[chunk.chunk_id] = intro.chunk.chunk_id
    omissions = {k: v for k, v in result.audit.budget_omissions.items() if k not in contexts}
    audit = result.audit.model_copy(
        update={"context_chunks": contexts, "budget_omissions": omissions}
    )
    return replace(
        prepared, search_result=result.model_copy(update={"hits": tuple(hits), "audit": audit})
    )
