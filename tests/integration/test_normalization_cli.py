import json
from pathlib import Path

from dietary_rag.cli import main
from dietary_rag.corpus.contracts import DocumentVersion, SourceEligibility
from dietary_rag.corpus.identity import sha256_bytes
from dietary_rag.ingestion.html import HTMLExtractor
from dietary_rag.ingestion.normalization import Normalizer
from dietary_rag.ingestion.normalization_contracts import NormalizationReview
from dietary_rag.settings import Settings


def prepare(tmp_path, registry, monkeypatch):
    name = "mn_cold_storage"
    path = Path(__file__).resolve().parents[1] / "fixtures/html" / f"{name}.html"
    content = path.read_bytes()
    source = registry.get(name)
    source = source.model_copy(
        update={
            "access": source.access.model_copy(
                update={"sha256": sha256_bytes(content), "bytes": len(content)}
            )
        }
    )
    registry = registry.model_copy(
        update={"sources": tuple(source if s.document_id == name else s for s in registry.sources)}
    )
    fields = {
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
    }
    document = DocumentVersion(
        **source.model_dump(include=fields),
        document_version_id="docv_normalization_cli_fixture",
        retrieved_at=source.access.checked_at,
        raw_sha256=source.access.sha256,
        eligibility=SourceEligibility.model_validate(
            source.selection.model_dump(exclude={"approved_pdf_for_milestone"})
        ),
    )
    native = HTMLExtractor(registry, name).extract_report(path, document)
    target = tmp_path / "native.json"
    target.write_text(native.model_dump_json(indent=2))
    monkeypatch.setattr(
        "dietary_rag.cli.load_settings", lambda *a, **k: Settings(data_dir=tmp_path / "data")
    )
    monkeypatch.setattr("dietary_rag.cli.load_registry", lambda *a, **k: registry)
    return target, native, registry


def test_cli_pending_and_exact_review_exports_are_reproducible_without_catalog_writes(
    tmp_path, reviewed_registry, monkeypatch, capsys
):
    path, native, registry = prepare(tmp_path, reviewed_registry, monkeypatch)
    database = tmp_path / "data/staging/provenance.sqlite"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"untouched catalog sentinel")
    args = [
        "normalize-extractions",
        str(path),
        "--output-dir",
        str(tmp_path / "out"),
        "--report",
        str(tmp_path / "report.json"),
    ]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first == json.loads((tmp_path / "report.json").read_text())
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out) == first
    pending = Normalizer(registry).normalize(
        native, artifact_sha256=sha256_bytes(path.read_bytes())
    )
    receipt = NormalizationReview(
        normalization_id=pending.normalization_id,
        configuration_id=pending.configuration_id,
        original_extraction_id=native.extraction_id,
        raw_sha256=native.document.raw_sha256,
        block_ids=tuple(b.block_id for b in pending.blocks),
        reviewer="Project comparison",
        scope="Transformation/exclusion fidelity",
        note="Fixture review only.",
    )
    reviews = tmp_path / "reviews.json"
    reviews.write_text(json.dumps({"reviews": [receipt.model_dump(mode="json")]}))
    assert main(args + ["--review-file", str(reviews)]) == 0
    reviewed = json.loads(capsys.readouterr().out)["results"][0]
    assert reviewed["status"] == "reviewed" and reviewed["output"].endswith("-reviewed.json")
    assert database.read_bytes() == b"untouched catalog sentinel"
    assert len(list((tmp_path / "out").glob("*.json"))) == 2


def test_cli_report_selection_and_invalid_artifacts_are_explicit(
    tmp_path, reviewed_registry, monkeypatch, capsys
):
    path, native, registry = prepare(tmp_path, reviewed_registry, monkeypatch)
    report = tmp_path / "input-report.json"
    report.write_text(json.dumps({"results": [{"output": str(path)}]}))
    args = [
        "normalize-extractions",
        "--reports",
        str(report),
        "--output-dir",
        str(tmp_path / "out"),
    ]
    assert main(args) == 0
    capsys.readouterr()
    values = json.loads(path.read_text())
    values["blocks"][1]["text"] += " changed"
    path.write_text(json.dumps(values))
    assert main(args) == 1
    assert "identity" in json.loads(capsys.readouterr().out)["results"][0]["error"]
    assert main(["normalize-extractions"]) == 2
    assert "Choose explicit" in capsys.readouterr().out
