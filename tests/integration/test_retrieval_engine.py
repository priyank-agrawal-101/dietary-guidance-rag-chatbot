"""Real scoped Chroma queries and canonical BM25 using labeled deterministic vectors."""

import json
from collections import Counter
from pathlib import Path

import pytest
from conftest import _chunk_native_fixture, review_chunk_fixture

from dietary_rag.cli import main
from dietary_rag.corpus.chunk_repository import ChunkRepository
from dietary_rag.corpus.identity import sha256_bytes, stable_id
from dietary_rag.corpus.index_snapshot import build_candidate, vector_metadata
from dietary_rag.ingestion.chunking import StructureChunker
from dietary_rag.ingestion.html import HTMLExtractor
from dietary_rag.ingestion.normalization import Normalizer
from dietary_rag.providers.embeddings import passage_input
from dietary_rag.retrieval.contracts import RetrievalConfig, SearchRequest
from dietary_rag.retrieval.engine import Retriever, covered_by
from dietary_rag.retrieval.evaluation import load_frozen_cases
from dietary_rag.retrieval.resolution import DocumentResolver
from dietary_rag.settings import RuntimeSettings

ROOT = Path(__file__).resolve().parents[2]


class FixtureEmbedder:
    def __init__(self, tokenizer):
        self.tokenizer = tokenizer
        self.metadata = {"model_name": "test_only", "dimensions": 384, "metric": "cosine"}
        self.configuration_id = stable_id("embedding_config", self.metadata)
        self.calls = 0

    def input_record(self, chunk):
        text = passage_input(chunk)
        return {
            "chunk_id": chunk.chunk_id,
            "input_text": text,
            "input_sha256": sha256_bytes(text.encode()),
            "token_count": self.tokenizer.count(text),
        }

    def encode(self, texts):
        vectors = []
        for text in texts:
            vector = [0.0] * 384
            vector[int(sha256_bytes(text.encode())[:8], 16) % 384] = 1.0
            vectors.append(vector)
        return vectors

    def embed_query(self, question):
        self.calls += 1
        return self.encode([question])[0]


@pytest.fixture
def retrieval_inputs(tmp_path, reviewed_registry, monkeypatch, real_chunk_tokenizer):
    pytest.importorskip("chromadb")
    sources, natives = {}, []
    for name in ("mn_storage_chart", "mn_safe_temperatures"):
        normalizer, native = _chunk_native_fixture(reviewed_registry, name, monkeypatch)
        sources[name] = normalizer.registry.get(name)
        document = native.document.model_copy(
            update={"document_version_id": f"docv_fixture_{name}"}
        )
        natives.append(
            HTMLExtractor(normalizer.registry, name).extract_report(
                ROOT / f"tests/fixtures/html/{name}.html", document
            )
        )
    registry = reviewed_registry.model_copy(
        update={"sources": tuple(sources.get(s.document_id, s) for s in reviewed_registry.sources)}
    )
    normalizer = Normalizer(registry)
    inputs = []
    for native in natives:
        norm = review_chunk_fixture(normalizer, native)
        build = StructureChunker(real_chunk_tokenizer, registry).build(norm, native)
        inputs.append((build, norm, native))
    source = tmp_path / "chunks.sqlite"
    with ChunkRepository(source) as repository:
        snapshot = repository.save_snapshot(inputs, real_chunk_tokenizer)
    processor = FixtureEmbedder(real_chunk_tokenizer)
    directory = build_candidate(
        source,
        snapshot["snapshot_id"],
        tmp_path / "indexes",
        processor,
        real_chunk_tokenizer,
        registry,
    )
    resolver = DocumentResolver(
        registry, json.loads((ROOT / "config/document-aliases.json").read_text())
    )
    return directory, registry, real_chunk_tokenizer, processor, resolver


def open_retriever(inputs, strategy, **kwargs):
    directory, registry, tokenizer, processor, resolver = inputs
    config = RetrievalConfig(
        strategy=strategy, resolution_configuration_id=resolver.configuration_id
    )
    return Retriever(
        directory,
        registry,
        tokenizer,
        processor,
        config,
        resolver,
        RuntimeSettings(),
        query_embedder_factory=lambda: processor,
        **kwargs,
    )


@pytest.mark.parametrize("strategy", ["bm25", "dense", "hybrid"])
def test_all_modes_resolve_before_ranking_and_audit_actual_scope(retrieval_inputs, strategy):
    with open_retriever(retrieval_inputs, strategy) as retriever:
        request = SearchRequest(
            question="How long can hard cooked eggs be refrigerated?",
            selected_document_ids=("mn_storage_chart",),
        )
        result = retriever.search(request)
        assert result.hits
        assert result.audit.searched_document_ids == ("mn_storage_chart",)
        assert all(h.chunk.document_id == "mn_storage_chart" for h in result.hits)
        for hit in result.hits:
            resolved = retriever.evidence.resolve_chunk(hit.chunk.chunk_id)
            assert resolved["chunk"] == hit.chunk and resolved["native"]
        assert set(result.audit.branch_ranks) == (
            {"bm25", "dense"} if strategy == "hybrid" else {strategy}
        )
        if strategy == "hybrid":
            assert result.audit.fused_ranks
        searched = retriever.search(SearchRequest(question="food"))
        assert set(searched.audit.searched_document_ids) == {
            "mn_storage_chart",
            "mn_safe_temperatures",
        }
        assert retriever.processor.calls == (0 if strategy == "bm25" else 2)


@pytest.mark.parametrize("strategy", ["bm25", "dense", "hybrid"])
def test_invalid_scope_does_not_load_weights_or_search(retrieval_inputs, strategy):
    with open_retriever(retrieval_inputs, strategy) as retriever:
        cases = [
            SearchRequest(question="food", selected_document_ids=("unknown",)),
            SearchRequest(question="food", selected_document_ids=("mn_storage_chart", "unknown")),
            SearchRequest(question="food", document_names=("CDC",)),
            SearchRequest(question="food", document_names=("Healthy Eating Plate",)),
            SearchRequest(
                question="According to Minnesota storage chart, food?",
                selected_document_ids=("mn_safe_temperatures",),
            ),
        ]
        for request in cases:
            result = retriever.search(request)
            assert result.audit.status == "invalid_selection"
            assert not result.hits and not result.audit.searched_document_ids
        absent = retriever.search(
            SearchRequest(question="food", source_tiers=("institutional_reference",))
        )
        assert absent.audit.status == "no_eligible_documents"
        assert not absent.hits
        assert retriever.processor.calls == 0
        assert retriever.embedder is None and not retriever.lexical_cache


@pytest.mark.parametrize("strategy", ["bm25", "dense", "hybrid"])
def test_comparisons_cap_document_contributions_and_total(retrieval_inputs, strategy):
    with open_retriever(retrieval_inputs, strategy) as retriever:
        result = retriever.search(
            SearchRequest(question="food storage temperature", comparison=True, limit=20)
        )
        counts = Counter(h.document.document_id for h in result.hits)
        assert len(result.hits) <= 20 and max(counts.values()) <= 3
        assert len(result.audit.searched_document_ids) == 2
        assert result.audit.budget_omissions
        if strategy == "bm25":
            cached = next(iter(retriever.lexical_cache.values()))
            assert not cached.rank("passage")
            assert all("passage: " not in text for text in retriever.texts.values())


def test_dense_filters_reject_ineligible_and_mixed_snapshot_vectors(retrieval_inputs):
    with open_retriever(retrieval_inputs, "dense") as retriever:
        chunk = next(iter(retriever.chunks.values()))
        metadata = vector_metadata(
            retriever.builds[chunk.chunk_id],
            chunk,
            retriever.manifest["snapshot_id"],
            retriever.processor.configuration_id,
        )
        mutations = [
            {"eligible": False},
            {"corpus_version": "wrong_snapshot"},
            {"content_role": "comparison_only"},
            {"target_audience": "non-US"},
            {"rights_status": "restricted"},
            {"source_tier": "institutional_reference"},
            {"document_version_id": "wrong_version"},
            {"document_id": "unknown"},
            {"family_id": "wrong_family"},
        ]
        for i, mutation in enumerate(mutations):
            retriever.collection.add(
                ids=[f"injected_{i}"],
                embeddings=retriever.processor.encode(["food"]),
                metadatas=[{**metadata, **mutation}],
            )
        result = retriever.search(SearchRequest(question="food", limit=20))
        assert result.hits and all(not h.chunk.chunk_id.startswith("injected") for h in result.hits)
        # A vector impersonating in-scope metadata is still rejected by canonical membership.
        retriever.collection.add(
            ids=["spoofed_id"],
            embeddings=retriever.processor.encode(["food"]),
            metadatas=[metadata],
        )
        with pytest.raises(ValueError, match="outside mandatory scope"):
            retriever.search(SearchRequest(question="food", limit=20))


def test_configuration_compatibility_bounds_and_query_encoder(retrieval_inputs):
    retriever = open_retriever(retrieval_inputs, "dense")
    retriever.config = retriever.config.model_copy(
        update={"resolution_configuration_id": "wrong_resolver"}
    )
    with pytest.raises(ValueError, match="resolver"):
        with retriever:
            pass
    with open_retriever(retrieval_inputs, "dense") as retriever:
        with pytest.raises(ValueError, match="bounds"):
            retriever.search(SearchRequest(question="food", limit=21))
        with pytest.raises(ValueError, match="bounds"):
            retriever.search(SearchRequest(question="x" * 2001))
        wrong = FixtureEmbedder(retriever.tokenizer)
        wrong.metadata = {**wrong.metadata, "model_name": "other_model"}
        retriever.query_embedder_factory = lambda: wrong
        with pytest.raises(ValueError, match="query embedder"):
            retriever.search(SearchRequest(question="food"))
        assert wrong.calls == 0
        assert retriever.embedder is None
        with pytest.raises(ValueError, match="query embedder"):
            retriever.search(SearchRequest(question="food"))
        assert wrong.calls == 0


def test_deadline_exhaustion_is_a_service_failure(retrieval_inputs, monkeypatch):
    with open_retriever(retrieval_inputs, "bm25") as retriever:
        moments = iter((0.0, 61.0))
        monkeypatch.setattr(
            "dietary_rag.retrieval.engine.time.monotonic", lambda: next(moments, 61.0)
        )
        with pytest.raises(TimeoutError, match="deadline"):
            retriever.search(SearchRequest(question="food"))


@pytest.mark.parametrize("strategy", ["bm25", "dense", "hybrid"])
def test_official_only_narrows_before_ranking_and_invalidates_selected_secondary(
    retrieval_inputs, strategy
):
    with open_retriever(retrieval_inputs, strategy) as retriever:
        # Synthetic secondary-tier metadata exercises the scope boundary; the
        # real reviewed corpus currently contains only official guidance.
        secondary = "mn_safe_temperatures"
        retriever.documents[secondary] = retriever.documents[secondary].model_copy(
            update={"source_tier": "institutional_reference"}
        )
        result = retriever.search(SearchRequest(question="food", official_only=True))
        assert result.audit.searched_document_ids == ("mn_storage_chart",)
        assert secondary in result.audit.filtered_documents
        assert all(h.document.source_tier == "official_guidance" for h in result.hits)
        invalid = retriever.search(
            SearchRequest(
                question="food",
                official_only=True,
                selected_document_ids=("mn_storage_chart", secondary),
            )
        )
        assert invalid.audit.status == "invalid_selection" and not invalid.hits
        assert not invalid.audit.searched_document_ids


def test_table_rows_and_necessary_qualifications_never_deduplicated(retrieval_inputs):
    with open_retriever(retrieval_inputs, "bm25") as retriever:
        table = next(c for c in retriever.chunks.values() if c.table_rows is not None)
        assert not covered_by(table, table)
        prose = next(c for c in retriever.chunks.values() if c.table_rows is None)
        assert covered_by(prose, prose)
        assert not covered_by(
            prose.model_copy(update={"text": prose.text + " necessary condition"}), prose
        )
        assert not covered_by(prose.model_copy(update={"document_version_id": "other_docv"}), prose)


def test_freeze_hash_blocks_changed_questions_before_retrieval(tmp_path):
    regressions = ROOT / "tests/fixtures/chunking/queries.json"
    heldout = tmp_path / "heldout.json"
    heldout.write_text('{"cases": []}')
    with pytest.raises(ValueError, match="frozen review"):
        load_frozen_cases(
            regressions,
            heldout,
            ROOT / "tests/fixtures/retrieval/freeze.json",
            {"chunk_snapshot": {"snapshot_id": "wrong_snapshot"}},
            {},
        )


def test_search_cli_requires_explicit_strategy(capsys):
    assert main(["search-corpus", "food"]) == 2
    assert "Choose --strategy" in capsys.readouterr().out
