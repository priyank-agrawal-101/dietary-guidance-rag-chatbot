"""Explicit staging chunk snapshots; acquisition schema and active corpus stay untouched."""

import json
import sqlite3
from pathlib import Path

from dietary_rag.corpus.identity import stable_id
from dietary_rag.corpus.repository import ProvenanceConflict
from dietary_rag.ingestion.chunk_contracts import Chunk, ChunkBuild
from dietary_rag.ingestion.chunking import validate_chunk_links
from dietary_rag.ingestion.normalization import parse_extraction
from dietary_rag.ingestion.normalization_contracts import NormalizedExtraction

SCHEMA = """
BEGIN;
CREATE TABLE normalized_inputs(normalization_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL);
CREATE TABLE native_inputs(extraction_id TEXT PRIMARY KEY,artifact_sha256 TEXT NOT NULL,payload_json TEXT NOT NULL);
CREATE TABLE blocks(normalization_id TEXT NOT NULL REFERENCES normalized_inputs(normalization_id),block_id TEXT NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(normalization_id,block_id));
CREATE TABLE builds(build_id TEXT PRIMARY KEY,normalization_id TEXT NOT NULL REFERENCES normalized_inputs(normalization_id),extraction_id TEXT NOT NULL REFERENCES native_inputs(extraction_id),role TEXT NOT NULL,payload_json TEXT NOT NULL);
CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY,build_id TEXT NOT NULL REFERENCES builds(build_id),payload_json TEXT NOT NULL);
CREATE TABLE snapshots(snapshot_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL);
CREATE TABLE snapshot_builds(snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),build_id TEXT NOT NULL REFERENCES builds(build_id),PRIMARY KEY(snapshot_id,build_id));
PRAGMA user_version=1;
COMMIT;
"""


class ChunkRepository:
    def __init__(self, path, *, read_only=False):
        self.path = Path(path)
        self.read_only = read_only
        self.connection = None

    def __enter__(self):
        if self.read_only:
            if not self.path.exists():
                raise FileNotFoundError(self.path)
            self.connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.path)
        try:
            self.connection.execute("PRAGMA foreign_keys=ON")
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0 and not self.read_only:
                if self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchone():
                    raise ProvenanceConflict("refusing unknown chunk database")
                self.connection.executescript(SCHEMA)
            elif version != 1:
                raise ProvenanceConflict("unsupported chunk schema")
            if not self.connection.execute(
                "SELECT name FROM sqlite_master WHERE name='normalized_inputs'"
            ).fetchone():
                raise ProvenanceConflict(
                    "database is not a chunk store; acquisition catalog cannot be reused"
                )
        except Exception:
            self.connection.close()
            self.connection = None
            raise
        return self

    def __exit__(self, *args):
        self.connection.close()
        self.connection = None

    def _insert(self, table, key_field, key, payload, *, extra=()):
        row = self.connection.execute(
            f"SELECT payload_json FROM {table} WHERE {key_field}=?", (key,)
        ).fetchone()
        if row and json.loads(row[0]) != json.loads(payload):
            raise ProvenanceConflict("existing chunk evidence cannot be replaced")
        if row:
            for field, value in extra:
                stored = self.connection.execute(
                    f"SELECT {field} FROM {table} WHERE {key_field}=?", (key,)
                ).fetchone()[0]
                if stored != value:
                    raise ProvenanceConflict("existing chunk provenance cannot be replaced")
        columns = (key_field, *[k for k, v in extra], "payload_json")
        values = (key, *[v for k, v in extra], payload)
        self.connection.execute(
            f"INSERT OR IGNORE INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
            values,
        )

    def save_snapshot(self, entries, tokenizer):
        if self.read_only:
            raise ValueError("chunk store is read-only")
        if not entries or len({b.document.document_id for b, n, native in entries}) != len(entries):
            raise ValueError("snapshot requires unique selected documents")
        if len({b.configuration_id for b, n, native in entries}) != 1:
            raise ValueError("snapshot cannot mix chunk configurations")
        for build, norm, native in entries:
            if build.state != "ready" or norm.review_state != "reviewed":
                raise ValueError("unresolved chunks cannot be persisted")
            synthetic = bool(norm.fixture_kind and norm.fixture_kind != "original_pdf")
            if (
                build.role == "consumer"
                and (
                    norm.comparison_only or synthetic or not norm.document.eligibility.default_index
                )
                or build.role == "comparison"
                and (not norm.comparison_only or synthetic)
                or build.role == "fixture"
                and not synthetic
            ):
                raise ValueError("chunk snapshot role differs from reviewed source eligibility")
            if (
                build.normalization_id != norm.normalization_id
                or build.original_extraction_id != native.extraction_id
                or build.document != norm.document
                or native.document != norm.document
            ):
                raise ValueError("chunk snapshot evidence links differ")
            validate_chunk_links(build, norm, tokenizer)
        manifest = {
            "configuration_id": entries[0][0].configuration_id,
            "role": entries[0][0].role,
            "active": False,
            "build_ids": sorted(b.build_id for b, n, native in entries),
            "documents": sorted(b.document.document_id for b, n, native in entries),
            "normalization_ids": sorted(n.normalization_id for b, n, native in entries),
            "chunks": sum(len(b.chunks) for b, n, native in entries),
        }
        snapshot_id = stable_id("chunk_snapshot", manifest)
        with self.connection:
            for build, norm, native in entries:
                self._insert(
                    "normalized_inputs",
                    "normalization_id",
                    norm.normalization_id,
                    norm.model_dump_json(),
                )
                self._insert(
                    "native_inputs",
                    "extraction_id",
                    native.extraction_id,
                    native.model_dump_json(),
                    extra=(("artifact_sha256", norm.original_artifact_sha256),),
                )
                for block in norm.blocks:
                    row = self.connection.execute(
                        "SELECT payload_json FROM blocks WHERE normalization_id=? AND block_id=?",
                        (norm.normalization_id, block.block_id),
                    ).fetchone()
                    if row and json.loads(row[0]) != block.model_dump(mode="json"):
                        raise ProvenanceConflict("canonical block conflict")
                    self.connection.execute(
                        "INSERT OR IGNORE INTO blocks VALUES(?,?,?)",
                        (norm.normalization_id, block.block_id, block.model_dump_json()),
                    )
                self._insert(
                    "builds",
                    "build_id",
                    build.build_id,
                    build.model_dump_json(),
                    extra=(
                        ("normalization_id", norm.normalization_id),
                        ("extraction_id", native.extraction_id),
                        ("role", build.role),
                    ),
                )
                for chunk in build.chunks:
                    self._insert(
                        "chunks",
                        "chunk_id",
                        chunk.chunk_id,
                        chunk.model_dump_json(),
                        extra=(("build_id", build.build_id),),
                    )
            self._insert("snapshots", "snapshot_id", snapshot_id, json.dumps(manifest))
            for build, _norm, _native in entries:
                self.connection.execute(
                    "INSERT OR IGNORE INTO snapshot_builds VALUES(?,?)",
                    (snapshot_id, build.build_id),
                )
        return {"snapshot_id": snapshot_id, **manifest}

    def get_build(self, build_id):
        row = self.connection.execute(
            "SELECT payload_json FROM builds WHERE build_id=?", (build_id,)
        ).fetchone()
        if row is None:
            raise KeyError(build_id)
        return ChunkBuild.model_validate_json(row[0])

    def get_normalization(self, normalization_id):
        row = self.connection.execute(
            "SELECT payload_json FROM normalized_inputs WHERE normalization_id=?",
            (normalization_id,),
        ).fetchone()
        if row is None:
            raise KeyError(normalization_id)
        return NormalizedExtraction.model_validate_json(row[0])

    def snapshot_builds(self, snapshot_id):
        return tuple(
            self.get_build(row[0])
            for row in self.connection.execute(
                "SELECT build_id FROM snapshot_builds WHERE snapshot_id=? ORDER BY build_id",
                (snapshot_id,),
            )
        )

    def get_snapshot(self, snapshot_id):
        row = self.connection.execute(
            "SELECT payload_json FROM snapshots WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
        if row is None:
            raise ValueError("unknown chunk snapshot")
        manifest = json.loads(row[0])
        if stable_id("chunk_snapshot", manifest) != snapshot_id:
            raise ValueError("chunk snapshot identity mismatch")
        builds = self.snapshot_builds(snapshot_id)
        if (
            not builds
            or sorted(b.build_id for b in builds) != manifest["build_ids"]
            or sorted(b.document.document_id for b in builds) != manifest["documents"]
            or sorted(b.normalization_id for b in builds) != manifest["normalization_ids"]
            or sum(len(b.chunks) for b in builds) != manifest["chunks"]
            or any(
                b.configuration_id != manifest["configuration_id"] or b.role != manifest["role"]
                for b in builds
            )
        ):
            raise ValueError("chunk snapshot membership/configuration mismatch")
        return {"snapshot_id": snapshot_id, **manifest}

    def get_native(self, extraction_id):
        row = self.connection.execute(
            "SELECT payload_json FROM native_inputs WHERE extraction_id=?", (extraction_id,)
        ).fetchone()
        if row is None:
            raise KeyError(extraction_id)
        return parse_extraction(json.loads(row[0]))

    def resolve_chunk(self, chunk_id):
        row = self.connection.execute(
            "SELECT payload_json,build_id FROM chunks WHERE chunk_id=?", (chunk_id,)
        ).fetchone()
        if row is None:
            raise KeyError(chunk_id)
        build = self.get_build(row[1])
        return {
            "chunk": Chunk.model_validate_json(row[0]),
            "build": build,
            "normalization": self.get_normalization(build.normalization_id),
            "native": self.get_native(build.original_extraction_id),
        }
