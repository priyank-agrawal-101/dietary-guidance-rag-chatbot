"""Explicit chunk snapshots, byte-bound receipts and offline lexical diagnostics."""

import json
import sqlite3

import pytest

from dietary_rag.cli import main
from dietary_rag.corpus.chunk_repository import ChunkRepository
from dietary_rag.corpus.identity import sha256_bytes
from dietary_rag.corpus.repository import ProvenanceConflict
from dietary_rag.ingestion.chunk_evaluation import evaluate
from dietary_rag.ingestion.chunking import StructureChunker
from dietary_rag.ingestion.html_profiles import PROFILES as HTML_PROFILES
from dietary_rag.ingestion.pdf_profiles import PROFILES as PDF_PROFILES
from dietary_rag.settings import Settings


@pytest.mark.parametrize("name", [*HTML_PROFILES, *PDF_PROFILES])
def test_real_tokenizer_all_layouts_fit_and_preserve_fidelity(
    chunk_evidence, real_chunk_tokenizer, name
):
    normalizer, native, norm = chunk_evidence(name)
    chunker = StructureChunker(
        real_chunk_tokenizer,
        normalizer.registry,
        role="comparison" if norm.comparison_only else "consumer",
    )
    build = chunker.build(norm, native)
    assert build.state == "ready" and all(c.token_count <= 512 for c in build.chunks)
    if name == "mn_safe_grilling_pdf":
        tables = [c for c in build.chunks if c.table_rows]
        assert len(tables) == 1 and len(tables[0].table_rows.row_indices) == 6
        assert "Regulated facilities" in tables[0].text
    smaller = StructureChunker(
        real_chunk_tokenizer, normalizer.registry, target_tokens=250, role=chunker.role
    ).build(norm, native)
    assert smaller.state == "ready" and smaller.input_limit == 512


def entries(chunk_evidence, tokenizer, name="mn_cold_storage"):
    normalizer, native, norm = chunk_evidence(name)
    build = StructureChunker(tokenizer, normalizer.registry).build(norm, native)
    return build, norm, native, normalizer


def test_sqlite_snapshots_resolve_exact_blocks_native_layout_and_tables(
    tmp_path, chunk_evidence, real_chunk_tokenizer
):
    build, norm, native, _ = entries(chunk_evidence, real_chunk_tokenizer, "mn_safe_grilling_pdf")
    path = tmp_path / "chunks.sqlite"
    with ChunkRepository(path) as repository:
        snapshot = repository.save_snapshot([(build, norm, native)], real_chunk_tokenizer)
        assert snapshot == repository.save_snapshot([(build, norm, native)], real_chunk_tokenizer)
        assert repository.get_build(build.build_id) == build
        assert repository.get_normalization(norm.normalization_id) == norm
        assert repository.get_native(native.extraction_id) == native
        resolved = repository.resolve_chunk(build.chunks[0].chunk_id)
        assert resolved["chunk"] == build.chunks[0] and resolved["normalization"] == norm
        assert resolved["native"] == native
        assert repository.connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == len(
            build.chunks
        )
        assert repository.connection.execute("SELECT count(*) FROM blocks").fetchone()[0] == len(
            norm.blocks
        )
        assert repository.snapshot_builds(snapshot["snapshot_id"]) == (build,)
    with ChunkRepository(path, read_only=True) as repository:
        assert repository.get_native(native.extraction_id).layouts == native.layouts
        with pytest.raises(ValueError, match="read-only"):
            repository.save_snapshot([(build, norm, native)], real_chunk_tokenizer)
        with pytest.raises(KeyError):
            repository.get_build("missing")


def test_sqlite_never_reuses_acquisition_or_unknown_schema(tmp_path):
    for version in (0, 1, 2):
        path = tmp_path / f"unknown-{version}.sqlite"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE document_versions(id TEXT)")
            connection.execute(f"PRAGMA user_version={version}")
        before = path.read_bytes()
        with pytest.raises(ProvenanceConflict):
            with ChunkRepository(path):
                pass
        assert path.read_bytes() == before
    with pytest.raises(FileNotFoundError):
        with ChunkRepository(tmp_path / "missing", read_only=True):
            pass


def test_sqlite_transaction_rolls_back_conflicting_provenance(
    tmp_path, chunk_evidence, real_chunk_tokenizer
):
    build, norm, native, _ = entries(chunk_evidence, real_chunk_tokenizer)
    with ChunkRepository(tmp_path / "chunks.sqlite") as repository:
        repository.connection.execute(
            "INSERT INTO native_inputs VALUES(?,?,?)",
            (native.extraction_id, "0" * 64, native.model_dump_json()),
        )
        repository.connection.commit()
        with pytest.raises(ProvenanceConflict):
            repository.save_snapshot([(build, norm, native)], real_chunk_tokenizer)
        assert (
            repository.connection.execute("SELECT count(*) FROM normalized_inputs").fetchone()[0]
            == 0
        )
        assert repository.connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0
        with pytest.raises(ValueError, match="unique"):
            repository.save_snapshot([(build, norm, native)] * 2, real_chunk_tokenizer)


def prepare_cli(tmp_path, chunk_evidence, real_chunk_tokenizer, monkeypatch):
    build, norm, native, normalizer = entries(chunk_evidence, real_chunk_tokenizer)
    raw = tmp_path / "native.json"
    raw.write_text(native.model_dump_json())
    assert sha256_bytes(raw.read_bytes()) == norm.original_artifact_sha256
    output = tmp_path / "normalized.json"
    output.write_text(norm.model_dump_json())
    input_report = tmp_path / "inputs.json"
    input_report.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "document_id": native.document.document_id,
                        "input": str(raw),
                        "output": str(output),
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(
        "dietary_rag.cli.load_settings",
        lambda *a, **k: Settings(
            data_dir=tmp_path / "data",
            embedding={"revision": real_chunk_tokenizer.metadata["revision"]},
        ),
    )
    monkeypatch.setattr("dietary_rag.cli.load_registry", lambda *a, **k: normalizer.registry)
    monkeypatch.setattr(
        "dietary_rag.ingestion.chunk_commands.E5Tokenizer", lambda *a, **k: real_chunk_tokenizer
    )
    report = tmp_path / "report.json"
    database = tmp_path / "chunks.sqlite"
    args = [
        "chunk-sources",
        "--normalization-report",
        str(input_report),
        "--output-dir",
        str(tmp_path / "out"),
        "--report",
        str(report),
        "--database",
        str(database),
    ]
    return args, report, database, raw, output, normalizer, native


def test_cli_reproducible_exports_and_byte_mismatch_cannot_persist(
    tmp_path, chunk_evidence, real_chunk_tokenizer, monkeypatch, capsys
):
    args, report, database, raw, output, normalizer, native = prepare_cli(
        tmp_path, chunk_evidence, real_chunk_tokenizer, monkeypatch
    )
    acquisition = tmp_path / "data/staging/provenance.sqlite"
    acquisition.parent.mkdir(parents=True)
    acquisition.write_bytes(b"untouched acquisition sentinel")
    assert main(args) == 0
    before = report.read_bytes()
    assert main(args) == 0 and report.read_bytes() == before
    assert acquisition.read_bytes() == b"untouched acquisition sentinel"
    raw.write_text(raw.read_text() + "\n")
    assert main(args) == 1
    rejected = json.loads(report.read_text())
    assert rejected["snapshot"] is None and "bytes differ" in rejected["results"][0]["error"]
    with ChunkRepository(database, read_only=True) as repository:
        assert repository.connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 1
    capsys.readouterr()


def test_cli_pending_review_invalid_budget_and_wrong_revision_fail(
    tmp_path, chunk_evidence, real_chunk_tokenizer, monkeypatch, capsys
):
    args, report, database, raw, output, normalizer, native = prepare_cli(
        tmp_path, chunk_evidence, real_chunk_tokenizer, monkeypatch
    )
    assert main([*args, "--target-tokens", "513"]) == 2 and not database.exists()
    output.write_text(normalizer.normalize(native).model_dump_json())
    assert main(args) == 1 and not database.exists()
    monkeypatch.setattr(
        "dietary_rag.cli.load_settings",
        lambda *a, **k: Settings(
            data_dir=tmp_path / "data", chunking={"tokenizer_revision": "unreviewed"}
        ),
    )
    assert main(args) == 2
    assert "revision differs" in capsys.readouterr().out


def test_offline_comparison_requires_same_evidence_and_returns_deterministic_hits(
    tmp_path, chunk_evidence, real_chunk_tokenizer, monkeypatch, capsys
):
    args, first, database, raw, output, normalizer, native = prepare_cli(
        tmp_path, chunk_evidence, real_chunk_tokenizer, monkeypatch
    )
    assert main(args) == 0
    second = tmp_path / "250-report.json"
    args[args.index("--report") + 1] = str(second)
    assert main([*args, "--target-tokens", "250"]) == 0
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "cold-storage",
                        "question": "What refrigerator temperature keeps food cold?",
                        "document_ids": [native.document.document_id],
                        "required_phrases": ["40"],
                    }
                ]
            }
        )
    )
    report = tmp_path / "evaluation.json"
    compare = [
        "compare-chunks",
        "--baseline-report",
        str(first),
        "--candidate-report",
        str(second),
        "--cases",
        str(cases),
        "--report",
        str(report),
    ]
    assert main(compare) == 0
    before = report.read_bytes()
    assert main(compare) == 0 and report.read_bytes() == before
    evaluation = json.loads(before)
    assert (
        evaluation["baseline"]["complete_evidence_hits"]
        == evaluation["candidate"]["complete_evidence_hits"]
        == 1
    )
    assert evaluation["method"] == "offline_bm25"
    data = json.loads(second.read_text())
    data["snapshot"]["build_ids"] = []
    second.write_text(json.dumps(data))
    assert main(compare) == 2
    capsys.readouterr()


def test_evaluation_rejects_unattributable_expected_answers(chunk_evidence, real_chunk_tokenizer):
    build, norm, native, _ = entries(chunk_evidence, real_chunk_tokenizer)
    with pytest.raises(ValueError, match="lacks attributable"):
        evaluate(
            [build],
            [
                {
                    "id": "invalid",
                    "question": "Unknown?",
                    "document_ids": ["missing"],
                    "required_phrases": ["invented"],
                }
            ],
        )


def test_tokenizer_preparation_reuses_verified_cache_without_network(
    real_chunk_tokenizer, monkeypatch, capsys
):
    monkeypatch.setattr(
        "dietary_rag.ingestion.chunk_tokenizer.urlopen",
        lambda *a, **k: pytest.fail("verified cache must not make network requests"),
    )
    assert main(["prepare-tokenizer"]) == 0
    assert "pinned_tokenizer_ready" in capsys.readouterr().out


def test_explicit_comparison_selection_cannot_inflate_consumer_snapshot(
    tmp_path, chunk_evidence, real_chunk_tokenizer, monkeypatch, capsys
):
    normalizer, native, norm = chunk_evidence("mn_storage_chart_pdf")
    raw = tmp_path / "native.json"
    raw.write_text(native.model_dump_json())
    canonical = tmp_path / "normalized.json"
    canonical.write_text(norm.model_dump_json())
    report = tmp_path / "input-report.json"
    # An informational flag cannot turn comparison evidence into default evidence.
    report.write_text(
        json.dumps(
            {"results": [{"input": str(raw), "output": str(canonical), "comparison_only": False}]}
        )
    )
    monkeypatch.setattr("dietary_rag.cli.load_registry", lambda *a, **k: normalizer.registry)
    monkeypatch.setattr(
        "dietary_rag.ingestion.chunk_commands.E5Tokenizer", lambda *a, **k: real_chunk_tokenizer
    )
    database = tmp_path / "chunks.sqlite"
    result = tmp_path / "result.json"
    args = [
        "chunk-sources",
        "--normalization-report",
        str(report),
        "--database",
        str(database),
        "--report",
        str(result),
        "--output-dir",
        str(tmp_path / "out"),
    ]
    assert main(args) == 1 and not database.exists()
    assert (
        json.loads(result.read_text())["skipped"][0]["document_id"] == native.document.document_id
    )
    assert main([*args, "--comparison"]) == 0
    assert json.loads(result.read_text())["snapshot"]["role"] == "comparison"
    capsys.readouterr()
