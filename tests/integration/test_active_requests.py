"""Canonical selection checks and pinned requests across actual Chroma/config switches."""

import copy
import json
import shutil
from pathlib import Path

import pytest
import test_retrieval_engine as retrieval_fixtures

from dietary_rag.corpus.lifecycle import LifecycleStore
from dietary_rag.ingestion.chunk_tokenizer import E5Tokenizer
from dietary_rag.providers.embeddings import BGETextProcessor
from dietary_rag.retrieval.activation import validate_activation
from dietary_rag.retrieval.contracts import RetrievalConfig, SearchRequest
from dietary_rag.retrieval.evaluation import select_strategy
from dietary_rag.retrieval.resolution import DocumentResolver
from dietary_rag.settings import RuntimeSettings, load_settings

ROOT = Path(__file__).resolve().parents[2]
retrieval_inputs = retrieval_fixtures.retrieval_inputs


def test_inflight_search_and_citation_keep_their_configuration_during_switch(
    tmp_path, retrieval_inputs
):
    directory, registry, tokenizer, processor, resolver = retrieval_inputs
    manifest = json.loads((directory / "manifest.json").read_bytes())
    files = {}
    for name in ("regressions.json", "heldout.json", "freeze.json"):
        files[name] = tmp_path / name
        files[name].write_text(
            '{"kind":"synthetic publication proof for storage-only integration"}'
        )
    store = LifecycleStore(tmp_path / "active")

    def validation(d, config, report, cases):
        # The real complete-proof validator is exercised below on the reviewed
        # corpus. This two-document fixture only tests request lifecycle.
        return {
            "snapshot_id": manifest["snapshot_id"],
            "embedding_configuration_id": processor.configuration_id,
        }

    def publish(strategy):
        config = RetrievalConfig(
            strategy=strategy, resolution_configuration_id=resolver.configuration_id
        )
        artifact = {
            "corpus_snapshot_id": manifest["snapshot_id"],
            "retrieval_configuration_id": config.configuration_id,
            "configuration": config.model_dump(),
        }
        return store.activate(
            directory,
            artifact,
            {"selection_report_id": f"test_selection_{strategy}"},
            files,
            validation,
        )

    first = publish("bm25")
    old_pin = store.pin()
    with old_pin.open_request(
        registry,
        tokenizer,
        processor,
        resolver,
        RuntimeSettings(),
        query_embedder_factory=lambda: processor,
    ) as old_request:
        question = SearchRequest(
            question="How long can hard cooked eggs keep?",
            selected_document_ids=("mn_storage_chart",),
        )
        old_result = old_request.search(question)
        assert old_result.hits
        old_chunk = old_result.hits[0].chunk
        second = publish("hybrid")
        assert first.active_release_id != second.active_release_id
        new_pin = store.pin()
        with new_pin.open_request(
            registry,
            tokenizer,
            processor,
            resolver,
            RuntimeSettings(),
            query_embedder_factory=lambda: processor,
        ) as new_request:
            new_result = new_request.search(question)
            assert new_result.audit.strategy == "hybrid"
            assert new_result.audit.activation_generation == 2
            assert new_result.audit.release_id == second.active_release_id
            assert old_result.audit.strategy == "bm25"
            assert old_result.audit.activation_generation == 1
            assert old_result.audit.release_id == first.active_release_id
            assert old_request.resolve_evidence(old_chunk.chunk_id)["chunk"] == old_chunk
            assert (
                old_request.search(question).audit.retrieval_configuration_id
                == old_pin.release.retrieval_configuration_id
            )
            with pytest.raises(ValueError, match="not retrieved"):
                old_request.resolve_evidence("unretrieved_chunk")
            rolled = store.rollback(validation)
            assert store.pin().release.configuration.strategy == "bm25"
            assert rolled.generation == 3
            # A request opened on release 2 survives the rollback to release 1.
            assert new_request.search(question).audit.strategy == "hybrid"
            assert new_request.resolve_evidence(new_result.hits[0].chunk.chunk_id)["native"]


@pytest.fixture(scope="module")
def real_proof_inputs(reviewed_registry):
    pytest.importorskip("chromadb")
    report_path = ROOT / "data/staging/retrieval/p3-review/selection.json"
    model_dir = ROOT / "data/staging/models/bge-small-en-v1.5"
    if not report_path.exists() or not (model_dir / "model.safetensors").exists():
        pytest.skip(
            "prepare pinned local model/candidate and frozen comparison first; no test downloads"
        )
    settings = load_settings(ROOT / "config/settings.yaml", project_root=ROOT, environ={})
    registry = reviewed_registry
    processor = BGETextProcessor(
        model_dir, ROOT / "config/embedding-bge-small-en-v1.5.json", settings.embedding
    )
    tokenizer = E5Tokenizer(
        ROOT / "data/staging/tokenizers/e5-base-v2", ROOT / "config/tokenizer-e5-base-v2.json"
    )
    resolver = DocumentResolver(
        registry, json.loads((ROOT / "config/document-aliases.json").read_bytes())
    )
    selection = json.loads(report_path.read_bytes())
    directory = Path(
        json.loads((ROOT / "data/staging/index-build-report.json").read_bytes())["snapshot_dir"]
    )
    config = RetrievalConfig.model_validate(selection["selected_configuration"])
    return directory, config, selection, registry, processor, tokenizer, resolver


@pytest.fixture
def case_directory(tmp_path):
    for name, source in (
        ("regressions.json", "tests/fixtures/chunking/queries.json"),
        ("heldout.json", "tests/fixtures/retrieval/heldout.json"),
        ("freeze.json", "tests/fixtures/retrieval/freeze.json"),
    ):
        shutil.copyfile(ROOT / source, tmp_path / name)
    return tmp_path


def test_real_frozen_comparison_passes_activation_without_loading_query_weights(
    real_proof_inputs, case_directory
):
    d, c, s, registry, processor, tokenizer, resolver = real_proof_inputs
    validation = validate_activation(
        d, c, s, case_directory, registry, processor, tokenizer, resolver
    )
    assert validation["chunks"] == 269 and validation["pdf_documents"] == 6
    assert not hasattr(processor, "model")


@pytest.mark.parametrize(
    "mutation",
    [
        "identity",
        "missing_scopes",
        "false_complete_hit",
        "wrong_scope",
        "changed_question",
        "changed_frozen_file",
    ],
)
def test_changed_selection_claims_cannot_activate(real_proof_inputs, case_directory, mutation):
    d, c, original, registry, processor, tokenizer, resolver = real_proof_inputs
    selection = copy.deepcopy(original)
    if mutation == "identity":
        selection["selection_report_id"] = "changed_selection"
    elif mutation == "changed_frozen_file":
        (case_directory / "heldout.json").write_text('{"cases":[]}')
    else:
        report = next(r for r in selection["strategy_reports"] if r["strategy"] == "hybrid")
        row = report["results"][0]
        if mutation == "missing_scopes":
            report["scope_checks"].pop()
        elif mutation == "wrong_scope":
            row["audit"]["searched_document_ids"] = [row["top_chunks"][0]["document_id"]]
        elif mutation == "changed_question":
            row["question"] = "A different question"
        else:
            # Re-sign a report claiming a hit for a real but unrelated chunk.
            unrelated = report["results"][-1]["top_chunks"][0]
            row["top_chunks"][0].update(
                {k: unrelated[k] for k in ("chunk_id", "document_id", "chunk_type")}
            )
        selection = select_strategy(selection["strategy_reports"])
    with pytest.raises(ValueError):
        validate_activation(
            d, c, selection, case_directory, registry, processor, tokenizer, resolver
        )


def test_passing_but_unselected_configuration_is_not_activated(real_proof_inputs, case_directory):
    d, c, selection, registry, processor, tokenizer, resolver = real_proof_inputs
    bm25 = c.model_copy(update={"strategy": "bm25"})
    with pytest.raises(ValueError, match="selected passing"):
        validate_activation(
            d, bm25, selection, case_directory, registry, processor, tokenizer, resolver
        )
