from contextlib import contextmanager

import pytest
import test_retrieval_engine as retrieval_fixtures

from dietary_rag.api.contracts import ChatResponse, ServiceError
from dietary_rag.policy.classifier import TypedClassifierAdapter
from dietary_rag.policy.runtime import PolicyRuntime, PreparedRequest
from dietary_rag.policy.service import PolicyService
from dietary_rag.retrieval.contracts import (
    QueryModelUnavailable,
    RetrievalConfig,
    SearchAudit,
    SearchResult,
)
from dietary_rag.retrieval.engine import Retriever
from dietary_rag.settings import RuntimeSettings

retrieval_inputs = retrieval_fixtures.retrieval_inputs


def unopened():
    pytest.fail("policy/invalid input opened retrieval or generation resources")


@pytest.mark.parametrize(
    "question,status",
    [
        ("What foods treat diabetes?", "out_of_scope"),
        ("What is my ideal weight?", "out_of_scope"),
        ("How much protein is in an egg?", "requires_structured_data"),
        ("Is it safe?", "needs_clarification"),
    ],
)
def test_policy_exits_before_resources(question, status):
    with PolicyRuntime(unopened).prepare({"question": question}) as outcome:
        assert isinstance(outcome, ChatResponse) and outcome.status == status
        assert not outcome.citations and not outcome.searched_documents
        assert outcome.corpus_version is None


def test_invalid_scope_exits_before_resources():
    with PolicyRuntime(unopened).prepare(
        {"question": "Store food", "scope": {"mode": "selected", "document_ids": []}}
    ) as outcome:
        assert outcome.code == "invalid_request"


@pytest.mark.parametrize(
    "exception,code",
    [
        (RuntimeError("private-provider-secret"), "model_unavailable"),
        (TimeoutError("secret"), "request_timeout"),
    ],
)
def test_classifier_failure_is_error_not_gap(exception, code):
    def fail(question):
        raise exception

    policy = PolicyService(classifier=TypedClassifierAdapter(fail))
    with PolicyRuntime(unopened, policy=policy).prepare({"question": "Store food"}) as outcome:
        assert isinstance(outcome, ServiceError) and outcome.code == code
        assert "secret" not in outcome.model_dump_json()


@pytest.mark.parametrize(
    "exception,code",
    [
        (RuntimeError("secret"), "corpus_unavailable"),
        (QueryModelUnavailable("secret"), "model_unavailable"),
        (TimeoutError("secret"), "request_timeout"),
    ],
)
def test_retrieval_dependencies_are_errors(exception, code):
    @contextmanager
    def fail():
        raise exception
        yield

    with PolicyRuntime(fail).prepare({"question": "Store food"}) as outcome:
        assert outcome.code == code and "secret" not in outcome.model_dump_json()


def test_empty_hits_are_not_automatically_not_covered_and_context_survives():
    open_state = []

    class EmptySearch:
        def search(self, request):
            return SearchResult(
                hits=(),
                audit=SearchAudit(
                    status="searched",
                    corpus_snapshot_id="corpus",
                    retrieval_configuration_id="config",
                    strategy="bm25",
                    resolved_question=request.question,
                    selected_document_ids=None,
                    searched_document_ids=("doc",),
                ),
            )

    @contextmanager
    def opener():
        open_state.append(True)
        try:
            yield EmptySearch()
        finally:
            open_state.pop()

    with PolicyRuntime(opener).prepare({"question": "Store food"}) as outcome:
        assert isinstance(outcome, PreparedRequest) and open_state
    assert not open_state
    with pytest.raises(RuntimeError, match="caller failure"):
        with PolicyRuntime(opener).prepare({"question": "Store food"}):
            raise RuntimeError("caller failure")
    assert not open_state


def test_real_scope_validation_precedes_ranking_and_allowed_queries_retrieve(retrieval_inputs):
    directory, registry, tokenizer, processor, resolver = retrieval_inputs
    config = RetrievalConfig(
        strategy="hybrid", resolution_configuration_id=resolver.configuration_id
    )

    @contextmanager
    def opener():
        with Retriever(
            directory,
            registry,
            tokenizer,
            processor,
            config,
            resolver,
            RuntimeSettings(),
            query_embedder_factory=lambda: processor,
        ) as retriever:
            yield retriever

    runtime = PolicyRuntime(opener)
    question = "How long can hard cooked eggs keep?"
    for ids, code in [
        (["unknown_doc"], "unknown_document"),
        (["mn_storage_chart", "nhlbi_ckd"], "unknown_document"),
        (["mn_storage_chart", "fda_safe_handling_pdf"], "source_unavailable"),
    ]:
        with runtime.prepare(
            {"question": question, "scope": {"mode": "selected", "document_ids": ids}}
        ) as outcome:
            assert isinstance(outcome, ServiceError) and outcome.code == code
    assert processor.calls == 0
    with runtime.prepare(
        {"question": question, "scope": {"mode": "selected", "document_ids": ["mn_storage_chart"]}}
    ) as outcome:
        assert isinstance(outcome, PreparedRequest) and outcome.search_result.hits
        assert outcome.search_result.audit.searched_document_ids == ("mn_storage_chart",)
    assert processor.calls == 1
    with runtime.prepare({"question": "According to CDC, how should I store food?"}) as outcome:
        assert outcome.status == "needs_clarification"
    assert processor.calls == 1


def test_active_policy_request_pins_once_and_resolves_retrieved_evidence(
    tmp_path, retrieval_inputs
):
    import json

    from dietary_rag.corpus.lifecycle import LifecycleStore

    directory, registry, tokenizer, processor, resolver = retrieval_inputs
    config = RetrievalConfig(strategy="bm25", resolution_configuration_id=resolver.configuration_id)
    manifest = json.loads((directory / "manifest.json").read_text())
    store = LifecycleStore(tmp_path / "active")
    files = {}
    for name in ("regressions.json", "heldout.json", "freeze.json"):
        files[name] = tmp_path / name
        files[name].write_text('{"kind":"synthetic storage-only lifecycle proof"}')

    def validate(*args):
        return {
            "snapshot_id": manifest["snapshot_id"],
            "embedding_configuration_id": processor.configuration_id,
        }

    state = store.activate(
        directory,
        {
            "corpus_snapshot_id": manifest["snapshot_id"],
            "retrieval_configuration_id": config.configuration_id,
            "configuration": config.model_dump(),
        },
        {"selection_report_id": "test_policy_selection"},
        files,
        validate,
    )
    runtime = PolicyRuntime.for_active(
        store, registry, tokenizer, processor, resolver, RuntimeSettings()
    )
    with runtime.prepare(
        {
            "question": "How long can hard cooked eggs keep?",
            "scope": {"mode": "selected", "document_ids": ["mn_storage_chart"]},
        }
    ) as outcome:
        assert isinstance(outcome, PreparedRequest)
        assert outcome.search_result.audit.release_id == state.active_release_id
        chunk = outcome.search_result.hits[0].chunk
        assert outcome.evidence_request.resolve_evidence(chunk.chunk_id)["chunk"] == chunk
        with pytest.raises(ValueError, match="not retrieved"):
            outcome.evidence_request.resolve_evidence("unretrieved_chunk")
