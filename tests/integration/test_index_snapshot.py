"""Real Chroma storage with source-derived evidence and labeled test vectors."""

import json
import sqlite3

import pytest

from dietary_rag.cli import main
from dietary_rag.corpus.chunk_repository import ChunkRepository
from dietary_rag.corpus.identity import sha256_bytes, stable_id
from dietary_rag.corpus.index_snapshot import (
    build_candidate,
    chroma_client,
    close_chroma,
    validate_candidate,
)
from dietary_rag.ingestion.chunking import StructureChunker
from dietary_rag.providers.embeddings import passage_input


class TestEmbedder:
    """Deterministic storage fixture, never a dense retrieval quality claim."""

    __test__ = False

    def __init__(self, tokenizer):
        self.tokenizer = tokenizer
        self.metadata = {"model_name": "test_only", "dimensions": 384, "metric": "cosine"}
        self.configuration_id = stable_id("embedding_config", self.metadata)

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
            v = [0.0] * 384
            v[int(sha256_bytes(text.encode())[:8], 16) % 384] = 1.0
            vectors.append(v)
        return vectors


@pytest.fixture
def candidate_inputs(tmp_path, chunk_evidence, real_chunk_tokenizer):
    pytest.importorskip("chromadb", reason="install requirements/embeddings.lock")
    normalizer, native, norm = chunk_evidence("mn_safe_grilling_pdf")
    build = StructureChunker(real_chunk_tokenizer, normalizer.registry).build(norm, native)
    source = tmp_path / "source.sqlite"
    with ChunkRepository(source) as store:
        snapshot = store.save_snapshot([(build, norm, native)], real_chunk_tokenizer)
    return (
        source,
        snapshot["snapshot_id"],
        TestEmbedder(real_chunk_tokenizer),
        real_chunk_tokenizer,
        normalizer.registry,
    )


def candidate(tmp_path, inputs):
    source, snapshot, embedder, tokenizer, registry = inputs
    return build_candidate(source, snapshot, tmp_path / "indexes", embedder, tokenizer, registry)


def test_paired_snapshot_reproduces_source_provenance_and_is_idempotent(tmp_path, candidate_inputs):
    source, snapshot, embedder, tokenizer, registry = candidate_inputs
    before = source.read_bytes()
    path = candidate(tmp_path, candidate_inputs)
    report = validate_candidate(path, embedder, tokenizer, registry)
    assert report["active"] is False and report["retrieval_quality_evaluated"] is False
    assert report["pdf_documents"] == 1
    assert source.read_bytes() == before
    assert candidate(tmp_path, candidate_inputs) == path
    with ChunkRepository(path / "evidence.sqlite", read_only=True) as store:
        assert store.get_snapshot(snapshot)["chunks"] == report["chunks"]
        chunk = store.snapshot_builds(snapshot)[0].chunks[0]
        assert store.resolve_chunk(chunk.chunk_id)["chunk"] == chunk
    assert not list(path.parent.glob(".candidate-*"))


@pytest.mark.parametrize(
    "mutation",
    [
        "dimension",
        "input_hash",
        "chunk_missing",
        "vector_missing",
        "vector_changed",
        "metric",
        "metadata",
        "collection_metadata",
        "vector_membership",
    ],
)
def test_corrupted_candidate_is_rejected(tmp_path, candidate_inputs, mutation):
    _source, _snapshot, embedder, tokenizer, registry = candidate_inputs
    path = candidate(tmp_path, candidate_inputs)
    manifest = json.loads((path / "manifest.json").read_text())
    if mutation == "dimension":
        manifest["embedding"]["dimensions"] = 768
        (path / "manifest.json").write_text(json.dumps(manifest))
    elif mutation in ("input_hash", "chunk_missing"):
        with sqlite3.connect(path / "evidence.sqlite") as connection:
            if mutation == "chunk_missing":
                connection.execute(
                    "DELETE FROM chunks WHERE chunk_id=?", (manifest["chunk_ids"][0],)
                )
            else:
                connection.execute("UPDATE embedding_inputs SET payload_json='{}'")
    else:
        client = chroma_client(path / "chroma")
        try:
            collection = client.get_collection("dietary_evidence", embedding_function=None)
            identifier = manifest["chunk_ids"][0]
            if mutation == "vector_missing":
                collection.delete(ids=[identifier])
            elif mutation == "vector_changed":
                collection.update(ids=[identifier], embeddings=[[0.0] * 384])
            elif mutation == "metadata":
                collection.update(ids=[identifier], metadatas=[{"document_id": "another_source"}])
            elif mutation == "vector_membership":
                row = collection.get(ids=[identifier], include=["embeddings", "metadatas"])
                collection.delete(ids=[identifier])
                collection.add(
                    ids=["unexpected_chunk"],
                    embeddings=row["embeddings"],
                    metadatas=row["metadatas"],
                )
            elif mutation == "metric":
                row = collection.get(include=["embeddings", "metadatas"])
                metadata = collection.metadata
                client.delete_collection("dietary_evidence")
                collection = client.create_collection(
                    "dietary_evidence",
                    configuration={"hnsw": {"space": "l2"}},
                    metadata=metadata,
                    embedding_function=None,
                )
                collection.add(
                    ids=row["ids"], embeddings=row["embeddings"], metadatas=row["metadatas"]
                )
            else:
                collection.modify(metadata={"corpus_version": "another_snapshot"})
        finally:
            close_chroma(client)
    with pytest.raises((ValueError, sqlite3.Error)):
        validate_candidate(path, embedder, tokenizer, registry)


def test_inference_failure_cannot_publish_partial_snapshot(tmp_path, candidate_inputs):
    source, snapshot, embedder, tokenizer, registry = candidate_inputs
    embedder.encode = lambda texts: [[0.0] * 384 for _ in texts]
    with pytest.raises(ValueError, match="normalized"):
        build_candidate(source, snapshot, tmp_path / "indexes", embedder, tokenizer, registry)
    assert not (tmp_path / "indexes").exists()


def test_unknown_snapshot_rejected_before_inference(tmp_path, candidate_inputs):
    source, _snapshot, embedder, tokenizer, registry = candidate_inputs
    embedder.encode = lambda texts: pytest.fail("invalid snapshot reached inference")
    with pytest.raises(ValueError, match="unknown chunk snapshot"):
        build_candidate(source, "missing", tmp_path / "indexes", embedder, tokenizer, registry)


def test_candidate_copies_only_selected_snapshot(tmp_path, candidate_inputs):
    source, snapshot, embedder, tokenizer, registry = candidate_inputs
    with ChunkRepository(source) as store:
        first = store.snapshot_builds(snapshot)[0]
        norm = store.get_normalization(first.normalization_id)
        native = store.get_native(first.original_extraction_id)
        second = StructureChunker(tokenizer, registry, target_tokens=250).build(norm, native)
        alternate = store.save_snapshot([(second, norm, native)], tokenizer)
        assert alternate["snapshot_id"] != snapshot
    path = candidate(tmp_path, candidate_inputs)
    with ChunkRepository(path / "evidence.sqlite", read_only=True) as store:
        assert store.connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 1
        with pytest.raises(ValueError, match="unknown chunk snapshot"):
            store.get_snapshot(alternate["snapshot_id"])


def test_storage_failure_removes_unpublished_pair(tmp_path, candidate_inputs, monkeypatch):
    from dietary_rag.corpus import index_snapshot

    original = index_snapshot.chroma_client

    def failing_client(path):
        client = original(path)

        def fail(*a, **k):
            raise ValueError("injected vector storage failure")

        monkeypatch.setattr(client, "create_collection", fail)
        return client

    monkeypatch.setattr(index_snapshot, "chroma_client", failing_client)
    with pytest.raises(ValueError, match="storage failure"):
        candidate(tmp_path, candidate_inputs)
    assert list((tmp_path / "indexes").iterdir()) == []


def test_current_registry_change_rejects_old_snapshot_before_inference(tmp_path, candidate_inputs):
    source, snapshot, embedder, tokenizer, registry = candidate_inputs
    registry = registry.model_copy(
        update={
            "sources": tuple(
                s.model_copy(
                    update={"selection": s.selection.model_copy(update={"default_index": False})}
                )
                if s.document_id == "mn_safe_grilling_pdf"
                else s
                for s in registry.sources
            )
        }
    )
    embedder.encode = lambda texts: pytest.fail("ineligible source reached inference")
    with pytest.raises(ValueError):
        build_candidate(source, snapshot, tmp_path / "indexes", embedder, tokenizer, registry)


def test_index_cli_build_and_validation(tmp_path, candidate_inputs, monkeypatch, capsys):
    source, snapshot, embedder, tokenizer, registry = candidate_inputs
    monkeypatch.setattr("dietary_rag.cli.load_registry", lambda *a, **k: registry)
    monkeypatch.setattr("dietary_rag.providers.embeddings.BGEEmbedder", lambda *a, **k: embedder)
    monkeypatch.setattr(
        "dietary_rag.ingestion.chunk_tokenizer.E5Tokenizer", lambda *a, **k: tokenizer
    )
    report = tmp_path / "report.json"
    assert (
        main(
            [
                "build-index",
                "--chunk-database",
                str(source),
                "--chunk-snapshot",
                snapshot,
                "--output-dir",
                str(tmp_path / "indexes"),
                "--report",
                str(report),
            ]
        )
        == 0
    )
    result = json.loads(report.read_text())
    assert main(["validate-index", "--snapshot-dir", result["snapshot_dir"]]) == 0
    assert "compatible_candidate_no_activation" in capsys.readouterr().out
