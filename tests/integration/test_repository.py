import sqlite3
from datetime import UTC, datetime

import pytest

from dietary_rag.corpus.artifacts import ArtifactStore
from dietary_rag.corpus.contracts import AcquisitionRecord, DocumentVersion, SourceEligibility
from dietary_rag.corpus.identity import stable_id
from dietary_rag.corpus.registry import SourceRegistry
from dietary_rag.corpus.repository import CorpusRepository, ProvenanceConflict

NOW = datetime(2026, 11, 11, 14, tzinfo=UTC)


def prepared_version(
    registry, store, content=b"Synthetic fixture original HTML bytes", document_id="mn_cold_storage"
):
    artifact = store.put_bytes(content)
    values = registry.model_dump()
    for source in values["sources"]:
        if source["document_id"] == document_id:
            source["access"]["sha256"] = artifact.sha256
            source["access"]["bytes"] = artifact.byte_count
    registry = SourceRegistry.model_validate(values)
    source = registry.get(document_id)
    record = AcquisitionRecord(
        document_id=source.document_id,
        requested_url=source.source_url,
        resolved_url=source.resolved_url,
        expected_format=source.format,
        detected_format=source.format,
        outcome="success",
        checked_at=NOW,
        retrieved_at=NOW,
        status_code=200,
        byte_count=artifact.byte_count,
        raw_sha256=artifact.sha256,
        raw_path=artifact.relative_path,
    )
    fields = {
        field: getattr(source, field)
        for field in (
            "document_id",
            "family_id",
            "document_name",
            "publisher",
            "source_url",
            "resolved_url",
            "format",
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
    }
    fields.update(
        raw_sha256=artifact.sha256,
        retrieved_at=NOW,
        eligibility=SourceEligibility.model_validate(
            source.selection.model_dump(exclude={"approved_pdf_for_milestone"})
        ),
    )
    fields["document_version_id"] = stable_id("docv", fields)
    if source.pdf is not None:
        fields.update(
            page_count=source.pdf.page_count, text_classification=source.pdf.text_classification
        )
    return registry, record, DocumentVersion.model_validate(fields)


def test_pdf_version_preserves_edition_and_physical_page_metadata(tmp_path, reviewed_registry):
    store = ArtifactStore(tmp_path / "raw")
    # Synthetic storage bytes only: signature/parser validation belongs to P1.4.
    content = b"%PDF-1.7\nSynthetic storage-contract fixture\x00\xff"
    registry, record, document = prepared_version(
        reviewed_registry, store, content, "dga_2025_2030_pdf"
    )
    with CorpusRepository(tmp_path / "db.sqlite", store) as repo:
        snapshot_id = repo.save_registry(registry)
        acquisition_id = repo.save_acquisition(record)
        repo.save_document_version(
            document, acquisition_id=acquisition_id, registry_snapshot_id=snapshot_id
        )
        restored = repo.get_document_version(document.document_version_id)
        assert restored.dates.edition == "2025–2030"
        assert restored.dates.publication_year is None
        assert restored.page_count == 10
        assert restored.text_classification == "selectable_text"
        assert store.read_bytes(restored.raw_sha256) == content


def test_registry_original_bytes_dates_and_versions_survive_reopen(tmp_path, reviewed_registry):
    store = ArtifactStore(tmp_path / "raw")
    db = tmp_path / "documents.sqlite"
    registry, record, document = prepared_version(reviewed_registry, store)
    repo = CorpusRepository(db, store)
    assert not db.exists()
    with repo:
        snapshot_id = repo.save_registry(registry)
        assert repo.save_registry(registry) == snapshot_id
        assert len(repo.document_ids()) == len(registry.sources)
        acquisition_id = repo.save_acquisition(record)
        assert repo.save_acquisition(record) == acquisition_id
        repo.save_document_version(
            document, acquisition_id=acquisition_id, registry_snapshot_id=snapshot_id
        )
        repo.save_document_version(
            document, acquisition_id=acquisition_id, registry_snapshot_id=snapshot_id
        )
        assert len(repo.document_versions(document.document_id)) == 1
    with CorpusRepository(db, store) as reopened:
        assert reopened.get_registry(snapshot_id) == registry
        assert reopened.get_acquisition(acquisition_id) == record
        assert reopened.get_document_version(document.document_version_id) == document
        assert (
            reopened.get_source(snapshot_id, "cleveland_balanced_diet").selection.decision
            == "blocked_usage"
        )
        assert reopened.get_source(
            snapshot_id, "niddk_ckd_nutrition"
        ).dates.structured_metadata_observed
        assert document.dates.publication_year is None
        assert (
            document.dates.source_stated_dates
            == registry.get("mn_cold_storage").dates.source_stated_dates
        )
        assert store.read_bytes(record.raw_sha256) == b"Synthetic fixture original HTML bytes"
        assert reopened.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_failure_metadata_does_not_become_a_document_version(tmp_path, reviewed_registry):
    store = ArtifactStore(tmp_path / "raw")
    with CorpusRepository(tmp_path / "db.sqlite", store) as repo:
        snapshot_id = repo.save_registry(reviewed_registry)
        failed = AcquisitionRecord(
            document_id="cdc_food_safety",
            requested_url=reviewed_registry.get("cdc_food_safety").source_url,
            expected_format="html",
            outcome="http_error",
            checked_at=NOW,
            status_code=403,
            byte_count=42,
            error="Access blocked",
        )
        acquisition_id = repo.save_acquisition(failed)
        assert repo.get_acquisition(acquisition_id) == failed
        assert not store.root.exists()
        source = reviewed_registry.get("cdc_food_safety")
        candidate = DocumentVersion(
            document_id=source.document_id,
            document_version_id="docv_failed",
            family_id=source.family_id,
            document_name=source.document_name,
            publisher=source.publisher,
            source_url=source.source_url,
            format="html",
            dates=source.dates,
            retrieved_at=NOW,
            source_tier=source.source_tier,
            jurisdiction=source.jurisdiction,
            target_audience=source.target_audience,
            intended_audience=source.intended_audience,
            content_role=source.content_role,
            eligibility=SourceEligibility.model_validate(
                source.selection.model_dump(exclude={"approved_pdf_for_milestone"})
            ),
            rights_review=source.rights_review,
            raw_sha256="a" * 64,
        )
        with pytest.raises(ProvenanceConflict, match="successful acquisition"):
            repo.save_document_version(
                candidate, acquisition_id=acquisition_id, registry_snapshot_id=snapshot_id
            )
        assert repo.document_versions("cdc_food_safety") == ()


@pytest.mark.parametrize(
    "change",
    [
        {"publisher": "Different authority"},
        {"dates": {"publication_year": 2026}},
        {"raw_sha256": "f" * 64},
        {"family_id": "different_family"},
        {"retrieved_at": datetime(2026, 11, 12, tzinfo=UTC)},
    ],
)
def test_mismatched_acquisition_or_review_is_rejected_without_version_write(
    tmp_path, reviewed_registry, change
):
    store = ArtifactStore(tmp_path / "raw")
    registry, record, document = prepared_version(reviewed_registry, store)
    invalid = DocumentVersion.model_validate(document.model_dump() | change)
    with CorpusRepository(tmp_path / "db.sqlite", store) as repo:
        snapshot_id = repo.save_registry(registry)
        acquisition_id = repo.save_acquisition(record)
        with pytest.raises(ProvenanceConflict):
            repo.save_document_version(
                invalid, acquisition_id=acquisition_id, registry_snapshot_id=snapshot_id
            )
        assert not repo.document_versions(document.document_id)


def test_new_review_versions_do_not_overwrite_existing_identity(tmp_path, reviewed_registry):
    store = ArtifactStore(tmp_path / "raw")
    registry, record, document = prepared_version(reviewed_registry, store)
    with CorpusRepository(tmp_path / "db.sqlite", store) as repo:
        old_snapshot = repo.save_registry(registry)
        acquisition_id = repo.save_acquisition(record)
        repo.save_document_version(
            document, acquisition_id=acquisition_id, registry_snapshot_id=old_snapshot
        )
        values = registry.model_dump()
        for source in values["sources"]:
            if source["document_id"] == document.document_id:
                source["dates"]["source_stated_dates"] += ("Additional source-stated review label",)
        new_registry = SourceRegistry.model_validate(values)
        new_snapshot = repo.save_registry(new_registry)
        new_fields = document.model_dump() | {"dates": new_registry.get(document.document_id).dates}
        with pytest.raises(ProvenanceConflict, match="cannot be overwritten"):
            repo.save_document_version(
                DocumentVersion.model_validate(new_fields),
                acquisition_id=acquisition_id,
                registry_snapshot_id=new_snapshot,
            )
        new_fields["document_version_id"] = stable_id(
            "docv", {k: v for k, v in new_fields.items() if k != "document_version_id"}
        )
        repo.save_document_version(
            DocumentVersion.model_validate(new_fields),
            acquisition_id=acquisition_id,
            registry_snapshot_id=new_snapshot,
        )
        assert len(repo.document_versions(document.document_id)) == 2
        assert repo.get_registry(old_snapshot) == registry
        assert repo.get_document_version(document.document_version_id) == document


def test_acquisition_rejects_missing_or_misattributed_artifact_and_unknown_identity(
    tmp_path, reviewed_registry
):
    store = ArtifactStore(tmp_path / "raw")
    registry, record, _ = prepared_version(reviewed_registry, store)
    with CorpusRepository(tmp_path / "db.sqlite", store) as repo:
        repo.save_registry(registry)
        with pytest.raises(ProvenanceConflict):
            repo.save_acquisition(record.model_copy(update={"byte_count": record.byte_count + 1}))
        with pytest.raises(KeyError):
            repo.save_acquisition(record.model_copy(update={"document_id": "unknown"}))
        assert repo.connection.execute("SELECT count(*) FROM acquisitions").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError):
            with repo.connection:
                repo.connection.execute("INSERT INTO acquisitions VALUES ('x','unknown',NULL,'{}')")


def test_incompatible_database_is_not_migrated_or_overwritten(tmp_path):
    db = tmp_path / "db.sqlite"
    with sqlite3.connect(db) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(ProvenanceConflict, match="schema version"):
        with CorpusRepository(db, ArtifactStore(tmp_path / "raw")):
            pass
    with sqlite3.connect(db) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_repository_requires_explicit_open_and_closes_connection(tmp_path):
    repo = CorpusRepository(tmp_path / "db.sqlite", ArtifactStore(tmp_path / "raw"))
    with pytest.raises(RuntimeError):
        repo.document_ids()
    with repo:
        assert repo.document_ids() == ()
    with pytest.raises(RuntimeError):
        repo.document_ids()


def test_readonly_repository_never_creates_database_and_rejects_writes(tmp_path, reviewed_registry):
    store = ArtifactStore(tmp_path / "raw")
    missing = tmp_path / "missing/db.sqlite"
    with pytest.raises(FileNotFoundError):
        with CorpusRepository(missing, store, read_only=True):
            pass
    assert not missing.parent.exists()
    database = tmp_path / "actual.sqlite"
    with CorpusRepository(database, store) as repo:
        repo.save_registry(reviewed_registry)
    before = database.read_bytes()
    with CorpusRepository(database, store, read_only=True) as repo:
        assert len(repo.document_ids()) == len(reviewed_registry.sources)
        with pytest.raises(sqlite3.OperationalError):
            repo.save_registry(reviewed_registry)
    assert before == database.read_bytes()
