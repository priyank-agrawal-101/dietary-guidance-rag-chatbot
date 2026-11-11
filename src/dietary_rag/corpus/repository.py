"""SQLite provenance records. No database/file is opened until a repository is entered."""

import sqlite3
from pathlib import Path

from dietary_rag.corpus.artifacts import ArtifactStore
from dietary_rag.corpus.contracts import AcquisitionRecord, DocumentVersion, SourceEligibility
from dietary_rag.corpus.identity import stable_id
from dietary_rag.corpus.registry import RegistrySource, SourceRegistry


class ProvenanceConflict(ValueError):
    """An existing identity or relationship cannot be replaced with different metadata."""


SCHEMA_VERSION = 1
SCHEMA = """
BEGIN;
CREATE TABLE documents (document_id TEXT PRIMARY KEY NOT NULL);
CREATE TABLE registry_snapshots (
    registry_snapshot_id TEXT PRIMARY KEY NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE registry_sources (
    registry_snapshot_id TEXT NOT NULL REFERENCES registry_snapshots(registry_snapshot_id),
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    payload_json TEXT NOT NULL,
    PRIMARY KEY (registry_snapshot_id, document_id)
);
CREATE TABLE artifacts (
    sha256 TEXT PRIMARY KEY NOT NULL,
    byte_count INTEGER NOT NULL CHECK (byte_count > 0),
    relative_path TEXT NOT NULL UNIQUE
);
CREATE TABLE acquisitions (
    acquisition_id TEXT PRIMARY KEY NOT NULL,
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    raw_sha256 TEXT REFERENCES artifacts(sha256),
    payload_json TEXT NOT NULL
);
CREATE TABLE document_versions (
    document_version_id TEXT PRIMARY KEY NOT NULL,
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    acquisition_id TEXT NOT NULL REFERENCES acquisitions(acquisition_id),
    registry_snapshot_id TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL REFERENCES artifacts(sha256),
    payload_json TEXT NOT NULL,
    FOREIGN KEY (registry_snapshot_id, document_id)
        REFERENCES registry_sources(registry_snapshot_id, document_id)
);
CREATE INDEX acquisitions_document ON acquisitions(document_id);
CREATE INDEX versions_document ON document_versions(document_id);
PRAGMA user_version = 1;
COMMIT;
"""


class CorpusRepository:
    def __init__(self, database_path: Path, artifacts: ArtifactStore, *, read_only: bool = False):
        self.database_path = database_path.resolve()
        self.artifacts = artifacts
        self.read_only = read_only
        self._connection: sqlite3.Connection | None = None

    def __enter__(self):
        if self._connection is not None:
            raise RuntimeError("repository is already open")
        if self.read_only:
            if not self.database_path.is_file():
                raise FileNotFoundError(self.database_path)
            connection = sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True)
        else:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0 and not self.read_only:
                if connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchone():
                    raise ProvenanceConflict("refusing an unversioned existing database")
                connection.executescript(SCHEMA)
            elif version != SCHEMA_VERSION:
                raise ProvenanceConflict("unsupported provenance database schema version")
        except Exception:
            connection.close()
            raise
        self._connection = connection
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.connection.close()
        self._connection = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("open the repository with a context manager first")
        return self._connection

    def save_registry(self, registry: SourceRegistry) -> str:
        registry = SourceRegistry.model_validate_json(registry.model_dump_json())
        snapshot_id = stable_id("registry", {"registry": registry})
        payload = registry.model_dump_json()
        with self.connection:
            existing = self.connection.execute(
                "SELECT payload_json FROM registry_snapshots WHERE registry_snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
            if existing and SourceRegistry.model_validate_json(existing[0]) != registry:
                raise ProvenanceConflict("registry snapshot identity has different metadata")
            self.connection.execute(
                "INSERT OR IGNORE INTO registry_snapshots VALUES (?,?)", (snapshot_id, payload)
            )
            for source in registry.sources:
                self.connection.execute(
                    "INSERT OR IGNORE INTO documents VALUES (?)", (source.document_id,)
                )
                self.connection.execute(
                    "INSERT OR IGNORE INTO registry_sources VALUES (?,?,?)",
                    (snapshot_id, source.document_id, source.model_dump_json()),
                )
        return snapshot_id

    def get_registry(self, snapshot_id: str) -> SourceRegistry:
        row = self.connection.execute(
            "SELECT payload_json FROM registry_snapshots WHERE registry_snapshot_id=?",
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise KeyError(snapshot_id)
        return SourceRegistry.model_validate_json(row[0])

    def get_source(self, snapshot_id: str, document_id: str) -> RegistrySource:
        row = self.connection.execute(
            "SELECT payload_json FROM registry_sources WHERE registry_snapshot_id=? AND document_id=?",
            (snapshot_id, document_id),
        ).fetchone()
        if row is None:
            raise KeyError((snapshot_id, document_id))
        return RegistrySource.model_validate_json(row[0])

    def document_ids(self) -> tuple[str, ...]:
        return tuple(
            row[0]
            for row in self.connection.execute(
                "SELECT document_id FROM documents ORDER BY document_id"
            )
        )

    def save_acquisition(self, record: AcquisitionRecord) -> str:
        record = AcquisitionRecord.model_validate_json(record.model_dump_json())
        if record.raw_sha256 is not None:
            artifact = self.artifacts.describe(record.raw_sha256)
            if (
                record.byte_count != artifact.byte_count
                or record.raw_path != artifact.relative_path
            ):
                raise ProvenanceConflict("acquisition artifact size/path differs from stored bytes")
        elif record.raw_path is not None:
            raise ProvenanceConflict("acquisition artifact metadata lacks its SHA-256")
        acquisition_id = stable_id("acq", {"acquisition": record})
        with self.connection:
            if not self.connection.execute(
                "SELECT 1 FROM documents WHERE document_id=?", (record.document_id,)
            ).fetchone():
                raise KeyError(record.document_id)
            if record.raw_sha256 is not None:
                self.connection.execute(
                    "INSERT OR IGNORE INTO artifacts VALUES (?,?,?)",
                    (artifact.sha256, artifact.byte_count, artifact.relative_path.as_posix()),
                )
                stored = self.connection.execute(
                    "SELECT byte_count,relative_path FROM artifacts WHERE sha256=?",
                    (artifact.sha256,),
                ).fetchone()
                if tuple(stored) != (artifact.byte_count, artifact.relative_path.as_posix()):
                    raise ProvenanceConflict("artifact identity has conflicting metadata")
            self.connection.execute(
                "INSERT OR IGNORE INTO acquisitions VALUES (?,?,?,?)",
                (acquisition_id, record.document_id, record.raw_sha256, record.model_dump_json()),
            )
            if self.get_acquisition(acquisition_id) != record:
                raise ProvenanceConflict("acquisition identity has conflicting metadata")
        return acquisition_id

    def get_acquisition(self, acquisition_id: str) -> AcquisitionRecord:
        row = self.connection.execute(
            "SELECT payload_json FROM acquisitions WHERE acquisition_id=?", (acquisition_id,)
        ).fetchone()
        if row is None:
            raise KeyError(acquisition_id)
        return AcquisitionRecord.model_validate_json(row[0])

    def save_document_version(
        self,
        document: DocumentVersion,
        *,
        acquisition_id: str,
        registry_snapshot_id: str,
    ) -> None:
        document = DocumentVersion.model_validate_json(document.model_dump_json())
        record = self.get_acquisition(acquisition_id)
        source = self.get_source(registry_snapshot_id, document.document_id)
        if (
            record.outcome != "success"
            or record.document_id != document.document_id
            or record.raw_sha256 != document.raw_sha256
            or record.detected_format != document.format
            or record.retrieved_at != document.retrieved_at
            or record.requested_url != document.source_url
            or (document.resolved_url is not None and document.resolved_url != record.resolved_url)
        ):
            raise ProvenanceConflict("document version does not match its successful acquisition")
        if (
            source.family_id != document.family_id
            or source.format != document.format
            or source.source_url != document.source_url
        ):
            raise ProvenanceConflict(
                "document source/family/format differs from its reviewed registry snapshot"
            )
        reviewed_fields = (
            "document_name",
            "publisher",
            "dates",
            "source_tier",
            "jurisdiction",
            "target_audience",
            "intended_audience",
            "content_role",
            "rights_review",
            "author",
            "medical_reviewer",
        )
        selection = SourceEligibility.model_validate(
            source.selection.model_dump(exclude={"approved_pdf_for_milestone"})
        )
        if (
            any(getattr(source, field) != getattr(document, field) for field in reviewed_fields)
            or document.eligibility != selection
        ):
            raise ProvenanceConflict(
                "document attribution/dates/eligibility differ from their registry review"
            )
        if source.access.sha256 is not None and source.access.sha256 != document.raw_sha256:
            raise ProvenanceConflict(
                "document bytes differ from the reviewed source; a new review is required"
            )
        if source.pdf is not None and (
            document.page_count != source.pdf.page_count
            or document.text_classification != source.pdf.text_classification
        ):
            raise ProvenanceConflict("document PDF inspection differs from its registry review")
        self.artifacts.describe(document.raw_sha256)
        expected = (
            document.document_id,
            acquisition_id,
            registry_snapshot_id,
            document.raw_sha256,
            document.model_dump_json(),
        )
        with self.connection:
            existing = self.connection.execute(
                "SELECT document_id,acquisition_id,registry_snapshot_id,raw_sha256,payload_json "
                "FROM document_versions WHERE document_version_id=?",
                (document.document_version_id,),
            ).fetchone()
            if existing is not None:
                if (
                    tuple(existing[:4]) != expected[:4]
                    or DocumentVersion.model_validate_json(existing[4]) != document
                ):
                    raise ProvenanceConflict("document version identity cannot be overwritten")
            else:
                self.connection.execute(
                    "INSERT INTO document_versions VALUES (?,?,?,?,?,?)",
                    (document.document_version_id, *expected),
                )

    def get_document_version(self, version_id: str) -> DocumentVersion:
        row = self.connection.execute(
            "SELECT payload_json FROM document_versions WHERE document_version_id=?", (version_id,)
        ).fetchone()
        if row is None:
            raise KeyError(version_id)
        return DocumentVersion.model_validate_json(row[0])

    def document_versions(
        self, document_id: str, *, registry_snapshot_id: str | None = None
    ) -> tuple[DocumentVersion, ...]:
        query = "SELECT payload_json FROM document_versions WHERE document_id=?"
        parameters = (document_id,)
        if registry_snapshot_id is not None:
            query += " AND registry_snapshot_id=?"
            parameters += (registry_snapshot_id,)
        query += " ORDER BY document_version_id"
        return tuple(
            DocumentVersion.model_validate_json(row[0])
            for row in self.connection.execute(query, parameters)
        )
