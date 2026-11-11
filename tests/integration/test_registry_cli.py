import json
from pathlib import Path

from dietary_rag.cli import main


def test_registry_cli_shows_all_decisions_without_creating_runtime_data(
    tmp_path, monkeypatch, capsys
):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(tmp_path)
    assert main(["check-registry", "--project-root", str(root), "--include-decisions"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["candidate_count"] == len(report["decisions"]) == 68
    assert report["consumer_ingestion_candidate_count"] == report["consumer_family_count"] == 16
    assert report["status"] == "reviewed_ingestion_candidates_only"
    rejected = next(
        item for item in report["decisions"] if item["document_id"] == "cleveland_balanced_diet"
    )
    assert not rejected["eligible_for_consumer_ingestion"]
    assert "rights_not_cleared_for_local_text" in rejected["reasons"]
    assert list(tmp_path.iterdir()) == []


def test_coverage_cli_is_readonly_and_missing_acquisition_store_is_explicit(
    tmp_path, monkeypatch, capsys
):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("DIETARY_RAG_DATA_DIR", str(tmp_path / "data"))
    assert main(["audit-coverage", "--project-root", str(root)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["eligible_consumer_family_count"] == 16
    assert report["consumer_acquisitions"] is None
    assert not report["published_corpus_ready"]
    assert not (tmp_path / "data").exists()
    assert main(["audit-coverage", "--project-root", str(root), "--verify-acquisitions"]) == 2
    assert not (tmp_path / "data").exists()
