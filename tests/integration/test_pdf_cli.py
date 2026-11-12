import json
from datetime import UTC, datetime
from pathlib import Path

from dietary_rag.cli import main
from dietary_rag.corpus.identity import sha256_bytes
from dietary_rag.corpus.registry import SourceRegistry
from dietary_rag.ingestion.acquisition import AcquisitionService
from dietary_rag.ingestion.pdf_profiles import REVIEWED_SOURCES
from dietary_rag.settings import Settings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/pdf"


def prepared(tmp_path, registry, monkeypatch, name="mn_cross_contamination_pdf"):
    content = (FIXTURES / (name + ".pdf")).read_bytes()
    values = registry.model_dump()
    for source in values["sources"]:
        if source["document_id"] == name:
            source["access"].update(sha256=sha256_bytes(content), bytes=len(content))
    registry = SourceRegistry.model_validate(values)
    source = registry.get(name)
    settings = Settings(data_dir=tmp_path / "data")
    monkeypatch.setitem(
        REVIEWED_SOURCES, name, {**REVIEWED_SOURCES[name], "raw_sha256": source.access.sha256}
    )
    service = AcquisitionService(
        registry, settings, clock=lambda: datetime(2026, 11, 12, tzinfo=UTC)
    )
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "source_id": name,
                "requested_url": str(source.source_url),
                "final_url": str(source.resolved_url),
                "checked_at": source.access.checked_at.isoformat(),
                "status": 200,
                "format": "pdf",
                "bytes": len(content),
                "sha256": sha256_bytes(content),
                "content_type": "application/pdf",
            }
        )
    )
    monkeypatch.setattr("dietary_rag.cli.load_settings", lambda *a, **k: settings)
    monkeypatch.setattr("dietary_rag.cli.load_registry", lambda path: registry)
    return service, receipt


def test_native_pdf_cli_exports_and_renders_without_catalog_mutation(
    tmp_path, reviewed_registry, monkeypatch, capsys
):
    service, receipt = prepared(tmp_path, reviewed_registry, monkeypatch)
    result = service.import_file(
        "mn_cross_contamination_pdf", FIXTURES / "mn_cross_contamination_pdf.pdf", receipt
    )
    assert result.status == "stored"
    before = service.database_path.read_bytes()
    output = tmp_path / "out"
    renders = tmp_path / "renders"
    report = tmp_path / "report.json"
    args = [
        "extract-pdf",
        "mn_cross_contamination_pdf",
        "--output-dir",
        str(output),
        "--render-dir",
        str(renders),
        "--report",
        str(report),
    ]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first == json.loads(report.read_text())
    row = first["results"][0]
    assert row["status"] == "extracted_pending_review" and not row["comparison_only"]
    assert row["document_version_id"] == result.document_version_id
    extraction = json.loads(Path(row["output"]).read_text())
    assert extraction["document"]["retrieved_at"] == service.registry.get(
        "mn_cross_contamination_pdf"
    ).access.checked_at.isoformat().replace("+00:00", "Z")
    assert row["renders"][0]["printed_page_label"] == "1"
    assert (
        Path(row["renders"][0]["original"]).is_file()
        and Path(row["renders"][0]["overlay"]).is_file()
    )
    assert main(args) == 0 and json.loads(capsys.readouterr().out) == first
    assert len(list(output.glob("*.json"))) == 1 and service.database_path.read_bytes() == before


def test_comparison_requires_explicit_original_receipt_and_never_creates_catalog(
    tmp_path, reviewed_registry, monkeypatch, capsys
):
    service, receipt = prepared(tmp_path, reviewed_registry, monkeypatch, "mn_storage_chart_pdf")
    output = tmp_path / "out"
    assert main(["extract-pdf", "mn_storage_chart_pdf", "--output-dir", str(output)]) == 2
    assert not output.exists()
    capsys.readouterr()
    args = [
        "extract-pdf",
        "mn_storage_chart_pdf",
        "--comparison",
        "--file",
        str(FIXTURES / "mn_storage_chart_pdf.pdf"),
        "--evidence",
        str(receipt),
        "--output-dir",
        str(output),
    ]
    assert main(args) == 0
    row = json.loads(capsys.readouterr().out)["results"][0]
    assert row["comparison_only"] and row["version_selection"] == "verified_original_receipt"
    assert not service.database_path.exists()
    wrong = json.loads(receipt.read_text())
    wrong["checked_at"] = "2026-11-12T00:00:00+00:00"
    receipt.write_text(json.dumps(wrong))
    assert main(args) == 1
    assert "receipt" in json.loads(capsys.readouterr().out)["results"][0]["error"]


def test_pdf_selection_and_missing_acquisition_fail_explicitly(
    tmp_path, reviewed_registry, monkeypatch, capsys
):
    service, _ = prepared(tmp_path, reviewed_registry, monkeypatch)
    assert main(["extract-pdf", "mn_cross_contamination_pdf", "unknown"]) == 2
    assert not service.database_path.exists()
    assert main(["extract-pdf", "mn_cross_contamination_pdf", "--all-eligible"]) == 2
    capsys.readouterr()
    assert (
        main(["extract-pdf", "mn_cross_contamination_pdf", "--output-dir", str(tmp_path / "out")])
        == 1
    )
    assert json.loads(capsys.readouterr().out)["results"][0]["status"] == "failed"
    assert not (tmp_path / "out").exists()
