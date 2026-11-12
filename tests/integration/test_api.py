"""HTTP integration using an actual pinned synthetic evidence/Chroma snapshot."""

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_answer_cli as answer_fixtures

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from dietary_rag.answering.service import AnswerService  # noqa: E402
from dietary_rag.api.app import create_app  # noqa: E402
from dietary_rag.api.context import CURRENT_REQUEST  # noqa: E402
from dietary_rag.api.contracts import service_error  # noqa: E402
from dietary_rag.api.runtime import ActiveRuntime, build_answer_service  # noqa: E402
from dietary_rag.cli import main  # noqa: E402
from dietary_rag.policy.runtime import PolicyRuntime  # noqa: E402
from dietary_rag.settings import (  # noqa: E402
    AnsweringSettings,
    APISettings,
    GenerationSettings,
    GroqSettings,
    RuntimeSettings,
    Settings,
)

active_answers = answer_fixtures.active_answers
retrieval_inputs = answer_fixtures.retrieval_inputs
ROOT = Path(__file__).resolve().parents[2]
QUESTION = "How long can hard cooked eggs stay in the refrigerator or freezer?"
PAYLOAD = {
    "question": QUESTION,
    "scope": {"mode": "selected", "document_ids": ["mn_storage_chart"]},
}


@pytest.fixture
def api_runtime(active_answers, monkeypatch, tmp_path):
    store, registry, tokenizer, processor, resolver = active_answers
    settings = Settings(data_dir=tmp_path, answering=AnsweringSettings(generator="extractive_v1"))
    monkeypatch.setattr(
        "dietary_rag.providers.embeddings.BGETextProcessor", lambda *args: processor
    )
    monkeypatch.setattr(
        "dietary_rag.ingestion.chunk_tokenizer.E5Tokenizer", lambda *args: tokenizer
    )
    active = ActiveRuntime(settings, registry, ROOT, lifecycle_dir=store.directory)
    return settings, registry, active


def client_for(api_runtime, **overrides):
    settings, registry, active = api_runtime
    return TestClient(
        create_app(
            settings=settings,
            registry=registry,
            active_runtime=active,
            project_root=ROOT,
            environ={},
            **overrides,
        )
    )


def test_api_and_cli_share_exact_validated_outcomes(api_runtime, monkeypatch, capsys):
    settings, registry, active = api_runtime
    direct = (
        AnswerService(PolicyRuntime(active.open_request)).answer(PAYLOAD).model_dump(mode="json")
    )
    monkeypatch.setattr("dietary_rag.cli.load_registry", lambda path: registry)
    assert (
        main(
            [
                "answer-question",
                QUESTION,
                "--offline",
                "--documents",
                "mn_storage_chart",
                "--lifecycle-dir",
                str(active.store.directory),
            ]
        )
        == 0
    )
    cli = json.loads(capsys.readouterr().out)
    with client_for(api_runtime) as client:
        result = client.post("/chat", json=PAYLOAD)
        api = result.json()
        assert result.status_code == 200
        assert api["request_id"] == result.headers["X-Request-ID"]
        for key in (
            "status",
            "document_answers",
            "citations",
            "searched_documents",
            "corpus_version",
        ):
            assert api[key] == cli[key] == direct[key]
        refusal = client.post("/chat", json={"question": "Set my ideal weight"})
        assert refusal.json()["status"] == "out_of_scope"
        assert refusal.json()["citations"] == []
        assert refusal.headers["X-Request-ID"] != result.headers["X-Request-ID"]


def test_liveness_readiness_and_available_document_metadata(api_runtime):
    with client_for(api_runtime) as client:
        assert client.get("/health/live").json() == {"status": "alive"}
        ready = client.get("/health/ready")
        assert ready.status_code == 200 and ready.json()["status"] == "ready"
        documents = client.get("/documents")
        assert documents.status_code == 200
        data = documents.json()
        assert data["corpus_version"] == ready.json()["corpus_version"]
        selected = next(d for d in data["documents"] if d["document_id"] == "mn_storage_chart")
        assert selected["available"] and selected["publisher"]
        assert selected["dates"]["publication_year"] is None
        assert (
            len([d for d in data["documents"] if d["available"]])
            == ready.json()["available_documents"]
        )
        assert any(not d["available"] for d in data["documents"])


@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_corpus_failure_is_distinct_from_liveness_and_lists_unavailable_sources(api_runtime, fault):
    _, _, active = api_runtime
    if fault == "missing":
        active.store.pointer.unlink()
    else:
        active.store.pointer.write_text('{"private":"secret"}')
    with client_for(api_runtime) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").json()["code"] == "corpus_unavailable"
        listing = client.get("/documents")
        assert listing.status_code == 503
        assert all(not d["available"] for d in listing.json()["documents"])
        allowed = client.post("/chat", json=PAYLOAD)
        assert allowed.status_code == 503 and allowed.json()["code"] == "corpus_unavailable"
        assert "secret" not in allowed.text
        refusal = client.post("/chat", json={"question": "What diet treats diabetes?"})
        assert refusal.status_code == 200 and refusal.json()["status"] == "out_of_scope"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"question": ""},
        {"question": "private", "secret": "never-echo"},
        {"question": "x" * 2001},
        {"question": "valid", "limit": 100},
    ],
)
def test_invalid_request_is_typed_and_does_not_echo_input(api_runtime, payload):
    with client_for(api_runtime) as client:
        result = client.post("/chat", json=payload)
        assert result.status_code == 422 and result.json()["code"] == "invalid_request"
        assert result.json()["request_id"] == result.headers["X-Request-ID"]
        assert "never-echo" not in result.text


def test_malformed_and_oversized_bodies_are_rejected(api_runtime):
    with client_for(api_runtime) as client:
        for content in ('{"private":"secret"', "x" * 17000):
            result = client.post("/chat", content=content)
            assert result.status_code == 422 and result.json()["code"] == "invalid_request"
            assert "secret" not in result.text


def test_scope_errors_preserve_http_codes_and_no_claims(api_runtime):
    with client_for(api_runtime) as client:
        result = client.post(
            "/chat",
            json={"question": QUESTION, "scope": {"mode": "selected", "document_ids": ["unknown"]}},
        )
        assert result.status_code == 404 and result.json()["code"] == "unknown_document"
        assert "document_answers" not in result.json()


def test_missing_groq_key_does_not_block_policy_or_document_listing(api_runtime):
    settings, registry, active = api_runtime
    settings = settings.model_copy(
        update={
            "generation": GenerationSettings(provider="groq", model_name="openai/gpt-oss-120b"),
            "answering": AnsweringSettings(generator="configured"),
        }
    )
    with client_for((settings, registry, active)) as client:
        assert client.get("/documents").status_code == 200
        assert client.get("/health/ready").json()["code"] == "model_unavailable"
        result = client.post("/chat", json=PAYLOAD)
        assert result.status_code == 503 and result.json()["code"] == "model_unavailable"
        assert (
            client.post("/chat", json={"question": "What diet treats diabetes?"}).status_code == 200
        )
        usage = client.get("/usage").json()
        assert usage["windows"]["day"]["requests"] == 0


def test_mocked_groq_preserves_citations_and_usage_limit_is_not_evidence_gap(api_runtime):
    import httpx

    settings, registry, active = api_runtime
    settings = settings.model_copy(
        update={
            "generation": GenerationSettings(provider="groq", model_name="openai/gpt-oss-120b"),
            "answering": AnsweringSettings(generator="configured"),
            "groq": GroqSettings(requests_per_minute=1, requests_per_hour=1, requests_per_day=1),
        }
    )
    observed = []

    def handle(request):
        observed.append(request)
        body = json.loads(request.content)
        evidence = json.loads(body["messages"][1]["content"])["evidence"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps({"unit_ids": [u["id"] for u in evidence]})
                        },
                    }
                ],
                "usage": {"total_tokens": 250},
            },
        )

    service, ledger = build_answer_service(
        settings,
        active,
        environ={"GROQ_API_KEY": "test-secret"},
        transport=httpx.MockTransport(handle),
    )
    with client_for((settings, registry, active), service=service, ledger=ledger) as client:
        result = client.post("/chat", json=PAYLOAD)
        assert result.status_code == 200 and result.json()["status"] == "answered"
        assert result.json()["citations"][0]["support_quotes"]
        limited = client.post("/chat", json=PAYLOAD)
        assert limited.status_code == 429 and limited.json()["code"] == "usage_limit_exceeded"
        assert int(limited.headers["Retry-After"]) >= 86399
        assert limited.json()["request_id"] == limited.headers["X-Request-ID"]
        assert len(observed) == 1
        assert client.get("/usage").json()["windows"]["day"]["tokens"] == 250
        assert "test-secret" not in result.text + limited.text


def test_http_deadline_returns_promptly_and_worker_capacity_remains_bounded(api_runtime):
    settings, registry, active = api_runtime
    settings = settings.model_copy(
        update={
            "runtime": RuntimeSettings(request_timeout_seconds=0.05),
            "api": APISettings(max_in_flight=1),
        }
    )
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def blocked(payload):
        entered.set()
        release.wait(1)
        finished.set()
        return service_error(CURRENT_REQUEST.get().request_id, "model_unavailable")

    service = SimpleNamespace(answer=blocked, generator=SimpleNamespace(name="fixture"))
    try:
        with client_for((settings, registry, active), service=service) as client:
            started = time.monotonic()
            result = client.post("/chat", json={"question": "valid"})
            assert entered.is_set() and result.status_code == 504
            assert time.monotonic() - started < 0.5
            assert result.json()["request_id"] == result.headers["X-Request-ID"]
            busy = client.post("/chat", json={"question": "valid"})
            assert busy.status_code == 503 and busy.json()["code"] == "service_busy"
            assert client.get("/health/live").status_code == 200
            release.set()
            assert finished.wait(1)
    finally:
        release.set()


def test_expired_retrieval_does_not_start_generation(api_runtime):
    settings, registry, active = api_runtime
    settings = settings.model_copy(
        update={"runtime": RuntimeSettings(request_timeout_seconds=0.02)}
    )
    searches = []

    @contextmanager
    def slow_open():
        time.sleep(0.06)
        yield SimpleNamespace(search=lambda request: searches.append(request))

    service = AnswerService(PolicyRuntime(slow_open, runtime=settings.runtime))
    with client_for((settings, registry, active), service=service) as client:
        assert client.post("/chat", json=PAYLOAD).status_code == 504
        time.sleep(0.08)
        assert searches == []


def test_openapi_publishes_shared_chat_request_and_error_schemas(api_runtime):
    with client_for(api_runtime) as client:
        result = client.get("/openapi.json")
        assert result.status_code == 200
        schema = result.json()
        request = schema["paths"]["/chat"]["post"]["requestBody"]
        assert (
            request["content"]["application/json"]["schema"]["$ref"]
            == "#/components/schemas/ChatRequest"
        )
        assert "ChatRequest" in schema["components"]["schemas"]
        assert "SelectedScope" in schema["components"]["schemas"]
        assert "429" in schema["paths"]["/chat"]["post"]["responses"]
        assert "504" in schema["paths"]["/chat"]["post"]["responses"]


def test_chat_page_and_assets_ship_with_security_headers(api_runtime):
    with client_for(api_runtime) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "text/html" in page.headers["content-type"]
        assert "What would you like to know?" in page.text
        assert "script-src 'self'" in page.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert page.headers["x-content-type-options"] == "nosniff"
        diagnostics = client.get("/diagnostics")
        assert diagnostics.status_code == 200
        assert 'role="tablist"' in diagnostics.text
        assert "script-src 'self'" in diagnostics.headers["content-security-policy"]
        assert diagnostics.headers["cache-control"] == "no-cache"
        assert 'href="/diagnostics"' in page.text
        assert "Usage &amp; limits" not in page.text
        for asset in (
            "app.js",
            "diagnostics.js",
            "client.js",
            "render.js",
            "style.css",
            "leaf.svg",
        ):
            result = client.get("/assets/" + asset)
            assert result.status_code == 200
            assert result.headers["referrer-policy"] == "no-referrer"
            assert "content-security-policy" in result.headers
        # API docs retain their own CDN policy; the chat CSP must not break them.
        assert "content-security-policy" not in client.get("/docs").headers


def test_ui_config_exposes_only_runtime_limits(api_runtime):
    settings, registry, active = api_runtime
    settings = settings.model_copy(
        update={
            "runtime": RuntimeSettings(
                question_max_characters=123, selected_document_limit=2, request_timeout_seconds=17
            ),
        }
    )
    with client_for((settings, registry, active)) as client:
        config = client.get("/ui/config")
        assert config.json() == {
            "question_max_characters": 123,
            "selected_document_limit": 2,
            "request_timeout_seconds": 17,
        }
        assert "GROQ_API_KEY" not in config.text


def test_ui_loads_during_corpus_outage_and_cannot_serve_project_secrets(api_runtime):
    _, _, active = api_runtime
    active.store.pointer.unlink()
    with client_for(api_runtime) as client:
        assert client.get("/").status_code == 200
        assert client.get("/health/ready").status_code == 503
        for path in (
            "/.env",
            "/assets/.env",
            "/assets/%2e%2e/config/settings.yaml",
            "/assets/%2e%2e/%2e%2e/.env",
            "/web/tests/ui.test.js",
        ):
            assert client.get(path).status_code == 404


def test_concurrent_readiness_and_listing_share_a_safe_corpus_lifecycle(api_runtime):
    from concurrent.futures import ThreadPoolExecutor

    _, _, active = api_runtime
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(active.corpus_status) for _ in range(2)]
        states = [future.result(timeout=10) for future in futures]
    assert states[0]["corpus_version"] == states[1]["corpus_version"]
    assert states[0]["documents"] == states[1]["documents"]


def test_waiting_for_corpus_access_obeys_the_request_deadline(api_runtime):
    from dietary_rag.api.context import request_context

    _, _, active = api_runtime
    active._corpus_lock.acquire()
    try:
        with request_context(0.02), pytest.raises(TimeoutError):
            with active.open_request():
                pytest.fail("expired request opened corpus resources")
    finally:
        active._corpus_lock.release()
