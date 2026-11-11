import json
from datetime import UTC, datetime

import httpx
import pytest

from dietary_rag.cli import main
from dietary_rag.corpus.identity import sha256_bytes
from dietary_rag.corpus.registry import SourceRegistry
from dietary_rag.corpus.repository import CorpusRepository
from dietary_rag.ingestion.acquisition import AcquisitionService
from dietary_rag.ingestion.network import Fetcher
from dietary_rag.settings import Settings

ORIGINAL = datetime(2025, 1, 2, 3, tzinfo=UTC)
NOW = datetime(2026, 11, 11, 16, tzinfo=UTC)
HTML = b"<html><main>" + b"Synthetic fixture food storage advice. " * 12 + b"</main></html>"


def prepared(tmp_path, registry, document_id="mn_cold_storage", content=HTML):
    values = registry.model_dump()
    for source in values["sources"]:
        if source["document_id"] == document_id:
            source["access"].update(sha256=sha256_bytes(content), bytes=len(content))
    registry = SourceRegistry.model_validate(values)
    source = registry.get(document_id)
    file = tmp_path / "original.bin"  # A suffix is deliberately not used to decide format.
    file.write_bytes(content)
    evidence = tmp_path / "receipt.json"
    evidence.write_text(
        json.dumps(
            {
                "source_id": document_id,
                "requested_url": str(source.source_url),
                "final_url": str(source.resolved_url),
                "checked_at": ORIGINAL.isoformat(),
                "status": 200,
                "format": source.format,
                "bytes": len(content),
                "sha256": sha256_bytes(content),
                "content_type": "application/pdf" if source.format == "pdf" else "text/html",
            }
        )
    )
    service = AcquisitionService(registry, Settings(data_dir=tmp_path / "data"), clock=lambda: NOW)
    return service, file, evidence


@pytest.mark.parametrize(
    "document_id,content", [("mn_cold_storage", HTML), ("mn_cross_contamination_pdf", None)]
)
def test_import_preserves_original_evidence_and_reuses_artifacts(
    tmp_path, reviewed_registry, document_id, content, pdf_factory
):
    content = content if content is not None else pdf_factory()
    service, file, receipt = prepared(tmp_path, reviewed_registry, document_id, content)
    first = service.import_file(document_id, file, receipt)
    second = service.import_file(document_id, file, receipt)
    assert first.status == second.status == "stored"
    assert first.acquisition_id == second.acquisition_id
    assert first.document_version_id == second.document_version_id
    assert first.processed_at == NOW
    with CorpusRepository(service.database_path, service.artifacts) as repo:
        record = repo.get_acquisition(first.acquisition_id)
        version = repo.get_document_version(first.document_version_id)
        assert record.retrieved_at == record.checked_at == ORIGINAL
        assert version.retrieved_at == ORIGINAL
        assert version.dates == reviewed_registry.get(document_id).dates
        assert service.artifacts.read_bytes(version.raw_sha256) == content
        assert len(repo.document_versions(document_id)) == 1
    assert len(list(service.artifacts.root.rglob("*.blob"))) == 1


def test_receipt_mismatch_and_html_disguised_as_pdf_never_create_versions(
    tmp_path, reviewed_registry
):
    service, file, receipt = prepared(tmp_path, reviewed_registry)
    file.write_bytes(HTML + b"modified")
    result = service.import_file("mn_cold_storage", file, receipt)
    assert result.failure_code == "receipt_hash_mismatch"
    assert not service.artifacts.root.exists()
    service, file, receipt = prepared(
        tmp_path, reviewed_registry, "mn_cross_contamination_pdf", HTML
    )
    result = service.import_file("mn_cross_contamination_pdf", file, receipt)
    assert result.failure_code == "format_mismatch"
    with CorpusRepository(service.database_path, service.artifacts) as repo:
        assert not repo.document_versions("mn_cross_contamination_pdf")


def test_valid_changed_copy_is_retained_for_review_not_autoapproved(tmp_path, reviewed_registry):
    service, file, receipt = prepared(tmp_path, reviewed_registry)
    # Receipt proves the file, but registry approval still describes the previous reviewed bytes.
    service.registry = reviewed_registry
    result = service.import_file("mn_cold_storage", file, receipt)
    assert result.status == "needs_review"
    assert "source_bytes_changed_since_review" in result.reasons
    assert result.document_version_id is None
    assert service.artifacts.read_bytes(sha256_bytes(HTML)) == HTML


def test_ineligible_source_is_blocked_before_files_or_network(tmp_path, reviewed_registry):
    service = AcquisitionService(reviewed_registry, Settings(data_dir=tmp_path / "data"))
    result = service.import_file(
        "cleveland_balanced_diet", tmp_path / "missing", tmp_path / "missing"
    )
    assert result.status == "blocked"
    assert service.fetch_source("niddk_ckd_nutrition").status == "blocked"
    assert not service.database_path.exists()


def test_fetch_reuses_blob_but_keeps_new_receipt_and_failures(tmp_path, reviewed_registry):
    service, file, receipt = prepared(tmp_path, reviewed_registry)
    original = service.import_file("mn_cold_storage", file, receipt)
    service.fetcher = Fetcher(
        service.settings.fetch,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=httpx.ByteStream(HTML))
        ),
    )
    fetched = service.fetch_source("mn_cold_storage")
    assert fetched.status == "stored"
    assert fetched.document_version_id != original.document_version_id
    assert len(list(service.artifacts.root.rglob("*.blob"))) == 1
    with CorpusRepository(service.database_path, service.artifacts) as repo:
        assert repo.get_acquisition(fetched.acquisition_id).retrieved_at == NOW
        assert len(repo.document_versions("mn_cold_storage")) == 2
    service.fetcher = Fetcher(
        service.settings.fetch, transport=httpx.MockTransport(lambda request: httpx.Response(404))
    )
    failed = service.fetch_source("mn_cold_storage")
    assert failed.status == "failed"
    with CorpusRepository(service.database_path, service.artifacts) as repo:
        assert repo.get_acquisition(failed.acquisition_id).status_code == 404
        assert len(repo.document_versions("mn_cold_storage")) == 2


def test_cli_import_report_and_invalid_selection(tmp_path, reviewed_registry, monkeypatch, capsys):
    service, file, receipt = prepared(tmp_path, reviewed_registry)
    monkeypatch.setattr("dietary_rag.cli.load_registry", lambda path: service.registry)
    monkeypatch.setattr("dietary_rag.cli.load_settings", lambda *a, **k: service.settings)
    report_path = tmp_path / "reports/run.json"
    assert (
        main(
            [
                "import-source",
                "mn_cold_storage",
                "--file",
                str(file),
                "--evidence",
                str(receipt),
                "--report",
                str(report_path),
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report == json.loads(report_path.read_text())
    assert report["counts"] == {"stored": 1}
    assert main(["fetch-sources"]) == 2
    assert main(["fetch-sources", "unknown"]) == 2
    assert main(["fetch-sources", "mn_cold_storage", "--all-eligible"]) == 2
    assert (
        main(
            [
                "import-source",
                "cleveland_balanced_diet",
                "--file",
                "missing",
                "--evidence",
                "missing",
            ]
        )
        == 1
    )


def test_coverage_verifies_current_snapshot_and_detects_artifact_corruption(
    tmp_path, reviewed_registry
):
    from dietary_rag.corpus.coverage import audit_coverage

    service, file, receipt = prepared(tmp_path, reviewed_registry)
    result = service.import_file("mn_cold_storage", file, receipt)
    with CorpusRepository(service.database_path, service.artifacts, read_only=True) as repo:
        report = audit_coverage(service.registry, repository=repo)
        assert report["consumer_acquisitions"]["matched_consumer_family_count"] == 1
        assert report["consumer_acquisitions"]["matched_approved_official_pdf_count"] == 0
        changed = service.registry.model_dump()
        changed["status"] = "new_review_snapshot"
        fresh_review = SourceRegistry.model_validate(changed)
        assert (
            audit_coverage(fresh_review, repository=repo)["consumer_acquisitions"][
                "matched_consumer_family_count"
            ]
            == 0
        )
    (service.artifacts.root / result.acquisition.raw_path).write_bytes(b"corrupted")
    with CorpusRepository(service.database_path, service.artifacts, read_only=True) as repo:
        report = audit_coverage(service.registry, repository=repo)
        assert report["consumer_acquisitions"]["matched_consumer_family_count"] == 0
        missing = next(
            item
            for item in report["consumer_acquisitions"]["missing_or_invalid"]
            if item["document_id"] == "mn_cold_storage"
        )
        assert "corrupt" in missing["reasons"][0]
