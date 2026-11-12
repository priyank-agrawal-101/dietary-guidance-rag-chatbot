import json
from datetime import UTC, datetime
from pathlib import Path

from dietary_rag.cli import main
from dietary_rag.corpus.registry import SourceRegistry
from dietary_rag.corpus.repository import CorpusRepository
from dietary_rag.ingestion.acquisition import AcquisitionService
from dietary_rag.settings import Settings

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/html/mn_cold_storage.html"


def imported(tmp_path, registry, monkeypatch):
    from dietary_rag.corpus.identity import sha256_bytes

    content = FIXTURE.read_bytes()
    values = registry.model_dump()
    for source in values["sources"]:
        if source["document_id"] == "mn_cold_storage":
            source["access"].update(sha256=sha256_bytes(content), bytes=len(content))
    registry = SourceRegistry.model_validate(values)
    settings = Settings(data_dir=tmp_path / "data")
    service = AcquisitionService(
        registry, settings, clock=lambda: datetime(2026, 11, 12, tzinfo=UTC)
    )
    source = registry.get("mn_cold_storage")
    receipt = tmp_path / "synthetic-receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "source_id": source.document_id,
                "requested_url": str(source.source_url),
                "final_url": str(source.resolved_url),
                "checked_at": "2026-11-11T12:00:00+00:00",
                "status": 200,
                "format": "html",
                "bytes": len(content),
                "sha256": sha256_bytes(content),
                "content_type": "text/html",
            }
        )
    )
    assert service.import_file("mn_cold_storage", FIXTURE, receipt).status == "stored"
    monkeypatch.setattr("dietary_rag.cli.load_registry", lambda path: registry)
    monkeypatch.setattr("dietary_rag.cli.load_settings", lambda *a, **k: settings)
    return service, receipt


def test_import_to_extraction_exports_are_repeatable_and_catalog_readonly(
    tmp_path, reviewed_registry, monkeypatch, capsys
):
    service, receipt = imported(tmp_path, reviewed_registry, monkeypatch)
    # Multiple original retrievals can legitimately share the same reviewed bytes.
    data = json.loads(receipt.read_text())
    data["checked_at"] = "2026-11-11T13:00:00+00:00"
    receipt.write_text(json.dumps(data))
    later = service.import_file("mn_cold_storage", FIXTURE, receipt)
    database_before = service.database_path.read_bytes()
    artifacts_before = sorted(service.artifacts.root.rglob("*.blob"))
    output = tmp_path / "exports"
    report = tmp_path / "report.json"
    args = ["extract-html", "mn_cold_storage", "--output-dir", str(output), "--report", str(report)]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first == json.loads(report.read_text())
    result = first["results"][0]
    assert result["document_version_id"] == later.document_version_id
    assert result["status"] == "extracted_pending_review"
    saved = Path(result["output"]).read_bytes()
    extraction = json.loads(saved)
    assert extraction["document"]["retrieved_at"] == "2026-11-11T13:00:00Z"
    assert extraction["document"]["dates"]["publication_year"] is None
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out) == first
    assert Path(result["output"]).read_bytes() == saved
    assert len(list(output.glob("*.json"))) == 1
    assert service.database_path.read_bytes() == database_before
    assert sorted(service.artifacts.root.rglob("*.blob")) == artifacts_before


def test_extraction_failure_reports_corrupt_or_missing_inputs_and_validates_selection(
    tmp_path, reviewed_registry, monkeypatch, capsys
):
    service, _ = imported(tmp_path, reviewed_registry, monkeypatch)
    output = tmp_path / "exports"
    assert main(["extract-html", "mn_cold_storage", "unknown", "--output-dir", str(output)]) == 2
    assert not output.exists()
    assert main(["extract-html", "fda_pregnancy_food_safety", "--output-dir", str(output)]) == 2
    assert not output.exists()
    capsys.readouterr()
    assert main(["extract-html", "mn_food_thermometers", "--output-dir", str(output)]) == 1
    missing = json.loads(capsys.readouterr().out)["results"][0]
    assert "no acquisition" in missing["error"]
    with CorpusRepository(service.database_path, service.artifacts, read_only=True) as repository:
        doc = repository.document_versions("mn_cold_storage")[0]
        artifact = service.artifacts.describe(doc.raw_sha256)
    (service.artifacts.root / artifact.relative_path).write_bytes(b"CORRUPTED")
    assert main(["extract-html", "mn_cold_storage", "--output-dir", str(output)]) == 1
    assert "SHA-256" in json.loads(capsys.readouterr().out)["results"][0]["error"]
    assert not output.exists()
