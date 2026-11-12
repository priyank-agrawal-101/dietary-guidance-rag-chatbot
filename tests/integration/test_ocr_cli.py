"""Actual Tesseract integration; ordinary routing/failure tests need no executable."""

import json
import shutil
from pathlib import Path

import pytest

from dietary_rag.cli import main
from dietary_rag.ingestion.ocr import OCRExtractionError, TesseractEngine
from dietary_rag.ingestion.ocr_contracts import OCRExtraction

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ocr"
TESSERACT = shutil.which("tesseract") or (
    "/opt/homebrew/bin/tesseract" if Path("/opt/homebrew/bin/tesseract").exists() else None
)


@pytest.mark.skipif(
    TESSERACT is None,
    reason="real OCR requires the separately installed Tesseract binary and eng data",
)
def test_real_mixed_ocr_cli_and_low_confidence_numeric_quarantine(tmp_path, capsys):
    args = [
        "extract-ocr",
        "--file",
        str(FIXTURES / "project-mixed-scan.pdf"),
        "--document",
        str(FIXTURES / "document.json"),
        "--plan",
        str(FIXTURES / "plan.json"),
        "--tesseract",
        TESSERACT,
        "--output-dir",
        str(tmp_path / "out"),
        "--report",
        str(tmp_path / "report.json"),
    ]
    assert main(args) == 1
    report = json.loads(capsys.readouterr().out)
    assert report == json.loads((tmp_path / "report.json").read_text())
    result = OCRExtraction.model_validate_json(Path(report["output"]).read_text())
    assert result.fixture_kind == "project_created_scan"
    assert result.engine["name"] == "tesseract" and result.engine["eng_traineddata_sha256"]
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    for page, lines in manifest["expected_good_text"].items():
        text = " ".join(
            b.text for b in result.blocks if b.source_locations[0].physical_page == int(page)
        )
        assert text == " ".join(lines)
    degraded = [c for c in result.candidates if c.source_location.physical_page == 8]
    assert degraded and all(c.state == "quarantined" and c.block_id is None for c in degraded)
    assert any("uncertain_numeric_or_unit" in c.reasons for c in degraded)
    assert not any(b.source_locations[0].physical_page == 8 for b in result.blocks)
    assert all(0 <= w.confidence <= 100 and w.location.bbox for w in result.words)
    assert main(args) == 1
    repeated = json.loads(capsys.readouterr().out)
    assert repeated == report
    assert OCRExtraction.model_validate_json(Path(repeated["output"]).read_text()) == result
    assert not (tmp_path / "out" / "provenance.sqlite").exists()


def test_ocr_cli_rejects_changed_bytes_and_incorrect_plan(tmp_path, capsys):
    path = tmp_path / "changed.pdf"
    path.write_bytes((FIXTURES / "project-mixed-scan.pdf").read_bytes() + b"changed")
    args = [
        "extract-ocr",
        "--file",
        str(path),
        "--document",
        str(FIXTURES / "document.json"),
        "--plan",
        str(FIXTURES / "plan.json"),
        "--output-dir",
        str(tmp_path / "out"),
    ]
    assert main(args) == 2
    assert "provenance" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()


def test_tesseract_missing_executable_fails_clearly(tmp_path):
    with pytest.raises(OCRExtractionError, match="inspection failed"):
        TesseractEngine(executable=tmp_path / "nonexistent")


@pytest.mark.skipif(TESSERACT is None, reason="language validation requires Tesseract")
def test_unknown_language_fails_before_rendering():
    with pytest.raises(OCRExtractionError, match="language data unavailable"):
        TesseractEngine(language="zzz", executable=TESSERACT)
