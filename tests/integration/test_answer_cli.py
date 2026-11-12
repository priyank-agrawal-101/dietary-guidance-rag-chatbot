"""Actual paired evidence/Chroma publication with synthetic fixture vectors, no downloads."""

import json
from pathlib import Path

import pytest
import test_retrieval_engine as retrieval_fixtures

from dietary_rag.answering.service import AnswerService
from dietary_rag.cli import main
from dietary_rag.corpus.lifecycle import LifecycleStore
from dietary_rag.policy.runtime import PolicyRuntime
from dietary_rag.retrieval.contracts import RetrievalConfig
from dietary_rag.settings import RuntimeSettings

ROOT = Path(__file__).resolve().parents[2]
retrieval_inputs = retrieval_fixtures.retrieval_inputs


@pytest.fixture
def active_answers(tmp_path, retrieval_inputs):
    directory, registry, tokenizer, processor, resolver = retrieval_inputs
    manifest = json.loads((directory / "manifest.json").read_text())
    config = RetrievalConfig(strategy="bm25", resolution_configuration_id=resolver.configuration_id)
    store = LifecycleStore(tmp_path / "active")
    files = {}
    for name in ("regressions.json", "heldout.json", "freeze.json"):
        files[name] = tmp_path / name
        files[name].write_text(
            '{"kind":"synthetic lifecycle proof for storage-only CLI integration"}'
        )

    def validation(*args):
        return {
            "snapshot_id": manifest["snapshot_id"],
            "embedding_configuration_id": processor.configuration_id,
        }

    store.activate(
        directory,
        {
            "corpus_snapshot_id": manifest["snapshot_id"],
            "retrieval_configuration_id": config.configuration_id,
            "configuration": config.model_dump(),
        },
        {"selection_report_id": "test_answer_selection"},
        files,
        validation,
    )
    return store, registry, tokenizer, processor, resolver


def test_cli_and_shared_service_have_same_validated_claims(
    active_answers, monkeypatch, capsys, tmp_path
):
    store, registry, tokenizer, processor, resolver = active_answers
    question = "How long can hard cooked eggs stay in the refrigerator or freezer?"
    runtime = PolicyRuntime.for_active(
        store, registry, tokenizer, processor, resolver, RuntimeSettings()
    )
    direct = AnswerService(runtime).answer(
        {"question": question, "scope": {"mode": "selected", "document_ids": ["mn_storage_chart"]}}
    )
    monkeypatch.setattr("dietary_rag.cli.load_registry", lambda path: registry)
    monkeypatch.setattr(
        "dietary_rag.providers.embeddings.BGETextProcessor", lambda *args: processor
    )
    monkeypatch.setattr(
        "dietary_rag.ingestion.chunk_tokenizer.E5Tokenizer", lambda *args: tokenizer
    )
    report = tmp_path / "answer-inspection.json"
    assert (
        main(
            [
                "answer-question",
                "--offline",
                question,
                "--documents",
                "mn_storage_chart",
                "--lifecycle-dir",
                str(store.directory),
                "--report",
                str(report),
            ]
        )
        == 0
    )
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "answered"
    assert response["document_answers"] == direct.model_dump(mode="json")["document_answers"]
    assert response["citations"] == direct.model_dump(mode="json")["citations"]
    inspection = json.loads(report.read_text())["inspection"]
    assert inspection["search_audit"]["release_id"] == store.pin().release.release_id
    assert inspection["generation_attempts"] == 1


def test_cli_refusal_does_not_require_active_corpus_or_models(tmp_path, capsys):
    assert (
        main(
            [
                "answer-question",
                "--offline",
                "Set my ideal weight",
                "--lifecycle-dir",
                str(tmp_path / "absent"),
                "--model-dir",
                str(tmp_path / "absent-model"),
            ]
        )
        == 0
    )
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "out_of_scope" and response["searched_documents"] == []
    assert response["citations"] == []


def test_cli_corpus_outage_is_a_service_failure(tmp_path, capsys):
    assert (
        main(
            [
                "answer-question",
                "--offline",
                "How should I store leftovers?",
                "--lifecycle-dir",
                str(tmp_path / "absent"),
            ]
        )
        == 1
    )
    response = json.loads(capsys.readouterr().out)
    assert response["code"] == "corpus_unavailable" and "status" not in response
