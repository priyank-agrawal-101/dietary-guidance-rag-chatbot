"""Immutable candidate snapshots pairing canonical evidence with Chroma vectors."""

import json
import math
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from dietary_rag.corpus.chunk_repository import ChunkRepository
from dietary_rag.corpus.identity import sha256_bytes, stable_id
from dietary_rag.ingestion.chunking import StructureChunker
from dietary_rag.providers.embeddings import validate_vectors

COLLECTION = "dietary_evidence"
SCHEMA_VERSION = 1


def chroma_client(directory):
    import chromadb
    from chromadb.config import Settings

    return chromadb.PersistentClient(
        path=str(Path(directory).resolve()),
        settings=Settings(anonymized_telemetry=False, allow_reset=False),
    )


def close_chroma(client):
    """Release persisted resources before moving a paired snapshot directory.

    Chroma 1.x versions without public close() require releasing their cached
    system. Remove only this client's identifier, preserving other snapshots.
    """
    if callable(getattr(client, "close", None)):
        client.close()
    else:
        from chromadb.api.shared_system_client import SharedSystemClient

        client._system.stop()
        SharedSystemClient._identifier_to_system.pop(client._identifier, None)


def reviewed_entries(repository, snapshot_id, tokenizer, registry):
    snapshot = repository.get_snapshot(snapshot_id)
    if snapshot["role"] != "consumer" or snapshot["active"]:
        raise ValueError("candidate index requires a reviewed consumer staging snapshot")
    entries = []
    families = set()
    for build in repository.snapshot_builds(snapshot_id):
        if build.document.family_id in families:
            raise ValueError("consumer snapshot contains duplicate families")
        families.add(build.document.family_id)
        norm = repository.get_normalization(build.normalization_id)
        native = repository.get_native(build.original_extraction_id)
        rebuilt = StructureChunker(
            tokenizer,
            registry,
            target_tokens=build.target_tokens,
            overlap_tokens=build.overlap_tokens,
            role="consumer",
        ).build(norm, native)
        if rebuilt != build or build.state != "ready" or norm.review_state != "reviewed":
            raise ValueError("chunk evidence/configuration differs from reviewed rebuild")
        for chunk in build.chunks:
            resolved = repository.resolve_chunk(chunk.chunk_id)
            if resolved != {
                "chunk": chunk,
                "build": build,
                "normalization": norm,
                "native": native,
            }:
                raise ValueError("chunk lookup differs from canonical snapshot evidence")
        entries.append((build, norm, native))
    return snapshot, entries


def vector_metadata(build, chunk, snapshot_id, configuration_id):
    document = build.document
    return {
        "document_id": document.document_id,
        "document_version_id": document.document_version_id,
        "family_id": document.family_id,
        "corpus_version": snapshot_id,
        "embedding_configuration_id": configuration_id,
        "source_tier": document.source_tier,
        "target_audience": document.target_audience,
        "content_role": document.content_role,
        "rights_status": document.rights_review.status,
        "eligible": True,
        "chunk_type": chunk.chunk_type,
    }


def _identity(manifest):
    return stable_id(
        "corpus_snapshot",
        {k: v for k, v in manifest.items() if k not in ("snapshot_id", "built_at")},
    )


def _vector_hash(vectors):
    return sha256_bytes(json.dumps(vectors, separators=(",", ":"), allow_nan=False).encode())


def document_membership(entries):
    return sorted(
        [
            {
                "document_id": build.document.document_id,
                "document_version_id": build.document.document_version_id,
                "raw_sha256": build.document.raw_sha256,
                "build_id": build.build_id,
                "normalization_id": build.normalization_id,
                "extraction_id": build.original_extraction_id,
            }
            for build, _norm, _native in entries
        ],
        key=lambda item: item["document_id"],
    )


def build_candidate(source_database, source_snapshot_id, output_dir, embedder, tokenizer, registry):
    with ChunkRepository(source_database, read_only=True) as source:
        source_manifest, entries = reviewed_entries(source, source_snapshot_id, tokenizer, registry)
    chunks = sorted(
        [(build, chunk) for build, _norm, _native in entries for chunk in build.chunks],
        key=lambda item: item[1].chunk_id,
    )
    records = [embedder.input_record(chunk) for _build, chunk in chunks]
    vectors = embedder.encode([r["input_text"] for r in records])
    validate_vectors(vectors, len(chunks))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "active": False,
        "role": "consumer",
        "chunk_snapshot": source_manifest,
        "documents": document_membership(entries),
        "embedding": embedder.metadata,
        "embedding_configuration_id": embedder.configuration_id,
        "chunk_ids": [chunk.chunk_id for _build, chunk in chunks],
        "input_records": records,
        "vectors_sha256": _vector_hash(vectors),
        "collection": COLLECTION,
        "collection_configuration": {"hnsw": {"space": "cosine"}},
    }
    manifest["snapshot_id"] = _identity(manifest)
    manifest["built_at"] = datetime.now(UTC).isoformat()
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / manifest["snapshot_id"]
    if destination.exists():
        validate_candidate(destination, embedder, tokenizer, registry)
        return destination
    temporary = Path(tempfile.mkdtemp(prefix=".candidate-", dir=root))
    client = None
    try:
        with ChunkRepository(temporary / "evidence.sqlite") as evidence:
            saved = evidence.save_snapshot(entries, tokenizer)
            if saved != source_manifest:
                raise ValueError("copied evidence snapshot differs from reviewed source")
            evidence.connection.execute(
                "CREATE TABLE embedding_inputs(chunk_id TEXT PRIMARY KEY REFERENCES chunks(chunk_id), "
                "payload_json TEXT NOT NULL, vector_json TEXT NOT NULL)"
            )
            evidence.connection.execute(
                "CREATE TABLE index_metadata(schema_version INTEGER NOT NULL, configuration_id TEXT NOT NULL)"
            )
            evidence.connection.execute(
                "INSERT INTO index_metadata VALUES(?,?)",
                (SCHEMA_VERSION, embedder.configuration_id),
            )
            evidence.connection.executemany(
                "INSERT INTO embedding_inputs VALUES(?,?,?)",
                [
                    (r["chunk_id"], json.dumps(r), json.dumps(v))
                    for r, v in zip(records, vectors, strict=True)
                ],
            )
            evidence.connection.commit()
        client = chroma_client(temporary / "chroma")
        collection = client.create_collection(
            COLLECTION,
            configuration=manifest["collection_configuration"],
            metadata={
                "corpus_version": manifest["snapshot_id"],
                "embedding_configuration_id": embedder.configuration_id,
            },
            embedding_function=None,
        )
        for start in range(0, len(chunks), 128):
            batch = chunks[start : start + 128]
            collection.add(
                ids=[chunk.chunk_id for _build, chunk in batch],
                embeddings=vectors[start : start + 128],
                metadatas=[
                    vector_metadata(
                        build, chunk, manifest["snapshot_id"], embedder.configuration_id
                    )
                    for build, chunk in batch
                ],
            )
        # Chroma >=1.0 persists automatically; close before moving the paired directory.
        close_chroma(client)
        client = None
        (temporary / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        report = validate_candidate(temporary, embedder, tokenizer, registry)
        (temporary / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
        temporary.rename(destination)
        return destination
    finally:
        if client is not None:
            close_chroma(client)
        if temporary.exists():
            shutil.rmtree(temporary)


def validate_candidate(directory, embedder, tokenizer, registry):
    root = Path(directory)
    manifest = json.loads((root / "manifest.json").read_text())
    if (
        manifest["schema_version"] != SCHEMA_VERSION
        or manifest["active"] is not False
        or manifest["role"] != "consumer"
        or manifest["snapshot_id"] != _identity(manifest)
        or manifest["embedding"] != embedder.metadata
        or manifest["embedding_configuration_id"] != embedder.configuration_id
        or manifest["collection"] != COLLECTION
        or manifest["collection_configuration"] != {"hnsw": {"space": "cosine"}}
    ):
        raise ValueError("candidate snapshot identity/model/configuration mismatch")
    if datetime.fromisoformat(manifest["built_at"]).utcoffset() is None:
        raise ValueError("snapshot build timestamp requires timezone")
    with ChunkRepository(root / "evidence.sqlite", read_only=True) as evidence:
        if evidence.connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("evidence database integrity failure")
        if evidence.connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("evidence foreign-key failure")
        source_manifest, entries = reviewed_entries(
            evidence, manifest["chunk_snapshot"]["snapshot_id"], tokenizer, registry
        )
        if source_manifest != manifest["chunk_snapshot"]:
            raise ValueError("candidate evidence snapshot mismatch")
        if manifest["documents"] != document_membership(entries):
            raise ValueError("candidate document-version provenance mismatch")
        chunks = sorted(
            [(build, chunk) for build, _norm, _native in entries for chunk in build.chunks],
            key=lambda item: item[1].chunk_id,
        )
        expected_ids = [chunk.chunk_id for _build, chunk in chunks]
        if manifest["chunk_ids"] != expected_ids:
            raise ValueError("candidate chunk membership mismatch")
        if evidence.connection.execute(
            "SELECT chunk_id FROM chunks ORDER BY chunk_id"
        ).fetchall() != [(i,) for i in expected_ids]:
            raise ValueError("candidate evidence contains extra/missing chunks")
        index_meta = evidence.connection.execute("SELECT * FROM index_metadata").fetchall()
        if index_meta != [(SCHEMA_VERSION, embedder.configuration_id)]:
            raise ValueError("evidence embedding configuration mismatch")
        rows = evidence.connection.execute(
            "SELECT chunk_id,payload_json,vector_json FROM embedding_inputs ORDER BY chunk_id"
        ).fetchall()
        records = [embedder.input_record(chunk) for _build, chunk in chunks]
        if (
            [row[0] for row in rows] != expected_ids
            or [json.loads(row[1]) for row in rows] != records
            or manifest["input_records"] != records
        ):
            raise ValueError("embedding input identity/hash/token-count mismatch")
        vectors = [json.loads(row[2]) for row in rows]
        validate_vectors(vectors, len(chunks))
        if _vector_hash(vectors) != manifest["vectors_sha256"]:
            raise ValueError("canonical embedding vector hash mismatch")
    if not (root / "chroma" / "chroma.sqlite3").is_file():
        raise ValueError("candidate vector index missing")
    client = chroma_client(root / "chroma")
    try:
        collection = client.get_collection(COLLECTION, embedding_function=None)
        if collection.metadata != {
            "corpus_version": manifest["snapshot_id"],
            "embedding_configuration_id": embedder.configuration_id,
        }:
            raise ValueError("vector collection model/snapshot mismatch")
        if collection.configuration_json["hnsw"]["space"] != "cosine":
            raise ValueError("vector collection metric mismatch")
        if collection.count() != len(chunks):
            raise ValueError("vector/chunk count mismatch")
        ids = []
        for start in range(0, len(chunks), 128):
            found = collection.get(
                limit=128, offset=start, include=["embeddings", "metadatas", "documents"]
            )
            by_id = {
                i: (v, m, d)
                for i, v, m, d in zip(
                    found["ids"],
                    found["embeddings"],
                    found["metadatas"],
                    found["documents"],
                    strict=True,
                )
            }
            for chunk_id, (vector, metadata, document) in by_id.items():
                if chunk_id not in expected_ids:
                    raise ValueError("vector membership differs from evidence")
                index = expected_ids.index(chunk_id)
                build, chunk = chunks[index]
                if (
                    metadata
                    != vector_metadata(
                        build, chunk, manifest["snapshot_id"], embedder.configuration_id
                    )
                    or document is not None
                ):
                    raise ValueError("vector metadata differs from canonical evidence")
                validate_vectors([vector], 1)
                if not all(
                    math.isclose(a, b, abs_tol=1e-7)
                    for a, b in zip(vector, vectors[index], strict=True)
                ):
                    raise ValueError("stored vector differs from canonical embedding")
            ids.extend(found["ids"])
        if sorted(ids) != expected_ids:
            raise ValueError("vector membership differs from evidence")
    finally:
        close_chroma(client)
    return {
        "status": "compatible_candidate_no_activation",
        "active": False,
        "snapshot_id": manifest["snapshot_id"],
        "chunks": len(chunks),
        "documents": len(entries),
        "pdf_documents": sum(b.document.format == "pdf" for b, _n, _v in entries),
        "embedding_configuration_id": embedder.configuration_id,
        "checks": {
            "exact_membership": True,
            "reviewed_provenance": True,
            "model_configuration": True,
            "complete_input_limits": True,
            "canonical_vectors": True,
            "scalar_metadata": True,
        },
        "retrieval_quality_evaluated": False,
    }
