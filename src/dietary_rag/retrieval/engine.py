"""Scoped Chroma/BM25/rank-fusion retrieval over one validated immutable snapshot."""

import json
import time
from pathlib import Path

from dietary_rag.corpus.chunk_repository import ChunkRepository
from dietary_rag.corpus.index_snapshot import (
    chroma_client,
    close_chroma,
    validate_candidate,
    vector_metadata,
)
from dietary_rag.corpus.registry import LOCAL_TEXT_RIGHTS
from dietary_rag.providers.embeddings import BGEEmbedder, validate_vectors
from dietary_rag.retrieval.contracts import (
    EvidenceHit,
    InvalidSearchRequest,
    QueryModelUnavailable,
    ScopeIssue,
    SearchAudit,
    SearchRequest,
    SearchResult,
)
from dietary_rag.retrieval.lexical import BM25


def reciprocal_rank_fusion(branches, constant):
    scores = {}
    for branch in branches.values():
        for rank, (identifier, _score) in enumerate(branch, 1):
            scores[identifier] = scores.get(identifier, 0.0) + 1 / (constant + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def covered_by(chunk, retained):
    """Only omit a lower-ranked prose/list slice when its original evidence is covered.

    Never deduplicate across documents/regions or discard different table rows.
    """
    if (
        chunk.document_version_id != retained.document_version_id
        or chunk.heading_path != retained.heading_path
        or chunk.region_keys != retained.region_keys
        or chunk.table_rows is not None
        or retained.table_rows is not None
        or chunk.text not in retained.text
    ):
        return False
    return all(
        any(
            original.block_id == span.block_id
            and original.start <= span.start
            and original.end >= span.end
            for original in retained.spans
        )
        for span in chunk.spans
    )


class Retriever:
    def __init__(
        self,
        directory,
        registry,
        tokenizer,
        processor,
        config,
        resolver,
        runtime,
        *,
        query_embedder_factory=None,
    ):
        self.directory = Path(directory)
        self.registry, self.tokenizer, self.processor = registry, tokenizer, processor
        self.config, self.resolver, self.runtime = config, resolver, runtime
        self.query_embedder_factory = query_embedder_factory
        self.embedder = None
        self.client = self.evidence = None
        self.lexical_cache = {}

    def __enter__(self):
        started = time.monotonic()
        if self.config.resolution_configuration_id != self.resolver.configuration_id:
            raise ValueError("retrieval configuration uses a different resolver/registry")
        self.validation = validate_candidate(
            self.directory, self.processor, self.tokenizer, self.registry
        )
        self.manifest = json.loads((self.directory / "manifest.json").read_text())
        try:
            self.evidence = ChunkRepository(
                self.directory / "evidence.sqlite", read_only=True
            ).__enter__()
            builds = self.evidence.snapshot_builds(self.manifest["chunk_snapshot"]["snapshot_id"])
            self.documents = {b.document.document_id: b.document for b in builds}
            self.chunks = {c.chunk_id: c for b in builds for c in b.chunks}
            self.builds = {c.chunk_id: b for b in builds for c in b.chunks}
            self.texts = {
                row[0]: json.loads(row[1])["input_text"]
                for row in self.evidence.connection.execute(
                    "SELECT chunk_id,payload_json FROM embedding_inputs"
                )
            }
            self.client = chroma_client(self.directory / "chroma")
            self.collection = self.client.get_collection(
                self.manifest["collection"], embedding_function=None
            )
            self.startup_seconds = time.monotonic() - started
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        if self.client is not None:
            close_chroma(self.client)
            self.client = None
        if self.evidence is not None:
            self.evidence.__exit__(*args)
            self.evidence = None

    def _where(self, documents):
        return {
            "$and": [
                {"corpus_version": self.manifest["snapshot_id"]},
                {"embedding_configuration_id": self.processor.configuration_id},
                {"eligible": True},
                {"target_audience": "US"},
                {"content_role": "consumer_guidance"},
                {"rights_status": {"$in": sorted(LOCAL_TEXT_RIGHTS)}},
                {"document_id": {"$in": sorted(documents)}},
                {"family_id": {"$in": sorted(self.documents[i].family_id for i in documents)}},
                {
                    "document_version_id": {
                        "$in": sorted(self.documents[i].document_version_id for i in documents)
                    }
                },
                {
                    "source_tier": {
                        "$in": sorted({self.documents[i].source_tier for i in documents})
                    }
                },
            ]
        }

    def _query_vector(self, question):
        if self.embedder is None:
            embedder = (
                self.query_embedder_factory()
                if self.query_embedder_factory
                else BGEEmbedder(
                    self.processor.directory, self.processor.manifest_path, self.processor.settings
                )
            )
            if embedder.metadata != self.processor.metadata:
                raise ValueError("query embedder differs from validated snapshot")
            self.embedder = embedder
        vector = self.embedder.embed_query(question)
        validate_vectors([vector], 1)
        return vector

    def _dense(self, documents, vector, comparison, deadline, omissions):
        rows = []
        groups = [{identifier} for identifier in sorted(documents)] if comparison else [documents]
        for group in groups:
            if time.monotonic() > deadline:
                raise TimeoutError("retrieval request deadline exceeded")
            count = sum(c.document_id in group for c in self.chunks.values())
            if not count:
                continue
            found = self.collection.query(
                query_embeddings=[vector],
                where=self._where(group),
                n_results=min(count, self.config.branch_candidates),
                include=["distances", "metadatas"],
            )
            ranked = []
            for identifier, distance, metadata in zip(
                found["ids"][0], found["distances"][0], found["metadatas"][0], strict=True
            ):
                if (
                    identifier not in self.chunks
                    or self.chunks[identifier].document_id not in group
                ):
                    raise ValueError("dense retrieval returned evidence outside mandatory scope")
                chunk = self.chunks[identifier]
                if metadata != vector_metadata(
                    self.builds[identifier],
                    chunk,
                    self.manifest["snapshot_id"],
                    self.processor.configuration_id,
                ):
                    raise ValueError("retrieved vector metadata differs from canonical evidence")
                ranked.append((identifier, 1 - distance))
            ranked.sort(key=lambda item: (-item[1], item[0]))
            if comparison:
                for identifier, _score in ranked[self.config.per_document_candidates :]:
                    omissions[identifier] = "dense_per_document_budget"
                ranked = ranked[: self.config.per_document_candidates]
            rows.extend(ranked)
        return sorted(rows, key=lambda item: (-item[1], item[0]))

    def _lexical(self, documents, question, comparison, omissions):
        key = tuple(sorted(documents))
        if key not in self.lexical_cache:
            if len(self.lexical_cache) >= 64:
                self.lexical_cache.clear()
            self.lexical_cache[key] = BM25(
                {i: self.texts[i] for i, c in self.chunks.items() if c.document_id in documents},
                k1=self.config.bm25_k1,
                b=self.config.bm25_b,
            )
        rows = self.lexical_cache[key].rank(question)
        if not comparison:
            return rows[: self.config.branch_candidates]
        selected, counts = [], {}
        for identifier, score in rows:
            document = self.chunks[identifier].document_id
            if counts.get(document, 0) >= self.config.per_document_candidates:
                omissions[identifier] = "bm25_per_document_budget"
                continue
            counts[document] = counts.get(document, 0) + 1
            selected.append((identifier, score))
        return selected

    def search(self, request):
        request = SearchRequest.model_validate(request)
        started = time.monotonic()
        deadline = started + self.runtime.request_timeout_seconds
        if (
            len(request.question) > self.runtime.question_max_characters
            or request.limit > self.config.candidate_chunks
        ):
            raise InvalidSearchRequest(
                "question or result limit exceeds configured retrieval bounds"
            )
        question, selected, issues = self.resolver.resolve(request, self.documents)
        if selected and len(selected) > self.runtime.selected_document_limit:
            raise InvalidSearchRequest("selected document limit exceeded")
        base = dict(
            corpus_snapshot_id=self.manifest["snapshot_id"],
            retrieval_configuration_id=self.config.configuration_id,
            strategy=self.config.strategy,
            resolved_question=question,
            selected_document_ids=selected,
        )
        if issues:
            return SearchResult(
                hits=(),
                audit=SearchAudit(
                    status="invalid_selection",
                    scope_issues=issues,
                    elapsed_seconds=time.monotonic() - started,
                    **base,
                ),
            )
        scope, filtered = set(), {}
        for identifier in selected if selected is not None else self.documents:
            document = self.documents[identifier]
            eligible = self.registry.consumer_eligibility(identifier)
            reason = None
            if not eligible.eligible_for_consumer_ingestion:
                reason = ", ".join(eligible.reasons)
            elif (
                document.content_role != "consumer_guidance"
                or document.target_audience != "US"
                or not (document.jurisdiction == "US" or document.jurisdiction.startswith("US-"))
                or document.rights_review.status not in LOCAL_TEXT_RIGHTS
            ):
                reason = "not_reviewed_us_consumer_evidence"
            elif request.official_only and document.source_tier != "official_guidance":
                reason = "official_only_filter"
            elif request.source_tiers and document.source_tier not in request.source_tiers:
                reason = "source_tier_filter"
            if reason:
                filtered[identifier] = reason
            else:
                scope.add(identifier)
        if selected is not None and filtered:
            issues = tuple(
                ScopeIssue(kind="unavailable", selection=i, candidate_document_ids=(i,), reason=r)
                for i, r in filtered.items()
            )
            return SearchResult(
                hits=(),
                audit=SearchAudit(
                    status="invalid_selection",
                    scope_issues=issues,
                    filtered_documents=filtered,
                    elapsed_seconds=time.monotonic() - started,
                    **base,
                ),
            )
        if not scope:
            return SearchResult(
                hits=(),
                audit=SearchAudit(
                    status="no_eligible_documents",
                    filtered_documents=filtered,
                    elapsed_seconds=time.monotonic() - started,
                    **base,
                ),
            )
        families = [self.documents[i].family_id for i in scope]
        if len(set(families)) != len(families):
            raise ValueError("retrieval scope contains duplicate canonical families")
        comparison = request.comparison or selected is not None and len(selected) > 1
        branches, omissions, duplicates = {}, {}, {}
        embedding_seconds = 0.0
        if self.config.strategy in ("dense", "hybrid"):
            before = time.monotonic()
            try:
                vector = self._query_vector(question)
            except (TimeoutError, ValueError):
                raise
            except Exception as exc:
                raise QueryModelUnavailable("query embedding dependency unavailable") from exc
            embedding_seconds = time.monotonic() - before
            branches["dense"] = self._dense(scope, vector, comparison, deadline, omissions)
        if self.config.strategy in ("bm25", "hybrid"):
            branches["bm25"] = self._lexical(scope, question, comparison, omissions)
        ranked = (
            reciprocal_rank_fusion(branches, self.config.rrf_constant)
            if self.config.strategy == "hybrid"
            else next(iter(branches.values()))
        )
        branch_positions = {
            name: {identifier: rank for rank, (identifier, _score) in enumerate(rows, 1)}
            for name, rows in branches.items()
        }
        retained, counts = [], {}
        for identifier, score in ranked:
            chunk = self.chunks[identifier]
            covering = next(
                (hit.chunk.chunk_id for hit in retained if covered_by(chunk, hit.chunk)), None
            )
            if covering:
                duplicates[identifier] = covering
                continue
            if (
                comparison
                and counts.get(chunk.document_id, 0) >= self.config.per_document_candidates
            ):
                omissions[identifier] = "final_per_document_budget"
                continue
            if len(retained) >= min(request.limit, self.config.candidate_chunks):
                omissions[identifier] = "final_candidate_budget"
                continue
            # Resolve original blocks/native locations from this pinned database before returning evidence.
            resolved = self.evidence.resolve_chunk(identifier)
            if (
                resolved["chunk"] != chunk
                or resolved["build"].document != self.documents[chunk.document_id]
            ):
                raise ValueError("retrieved canonical evidence changed within snapshot")
            retained.append(
                EvidenceHit(
                    chunk=chunk,
                    document=self.documents[chunk.document_id],
                    rank=len(retained) + 1,
                    score=score,
                    branch_ranks={
                        name: positions[identifier]
                        for name, positions in branch_positions.items()
                        if identifier in positions
                    },
                )
            )
            counts[chunk.document_id] = counts.get(chunk.document_id, 0) + 1
        if time.monotonic() > deadline:
            raise TimeoutError("retrieval request deadline exceeded")
        for hit in retained:
            omissions.pop(hit.chunk.chunk_id, None)

        def audit_rows(rows):
            return [
                {
                    "rank": rank,
                    "chunk_id": identifier,
                    "document_id": self.chunks[identifier].document_id,
                    "score": score,
                }
                for rank, (identifier, score) in enumerate(rows, 1)
            ]

        return SearchResult(
            hits=tuple(retained),
            audit=SearchAudit(
                status="searched",
                searched_document_ids=tuple(sorted(scope)),
                filtered_documents=filtered,
                branch_ranks={name: audit_rows(rows) for name, rows in branches.items()},
                fused_ranks=audit_rows(ranked) if self.config.strategy == "hybrid" else [],
                budget_omissions=omissions,
                duplicate_omissions=duplicates,
                query_embedding_seconds=embedding_seconds,
                elapsed_seconds=time.monotonic() - started,
                **base,
            ),
        )
