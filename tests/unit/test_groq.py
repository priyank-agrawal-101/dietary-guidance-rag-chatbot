"""Deterministic mocked Groq protocol tests; no credentials or paid API calls."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

httpx = pytest.importorskip("httpx")

from dietary_rag.answering.contracts import (  # noqa: E402
    AnswerModelUnavailable,
    AnswerValidationError,
    EvidenceUnit,
    GenerationInput,
    Support,
)
from dietary_rag.api.context import request_context  # noqa: E402
from dietary_rag.providers.groq import GroqGenerator, compact_input  # noqa: E402
from dietary_rag.providers.usage import (  # noqa: E402
    TokenBudgetExceeded,
    UsageLedger,
    UsageLimitExceeded,
)
from dietary_rag.settings import GroqSettings  # noqa: E402


@pytest.fixture
def generation():
    units = {
        doc: (
            EvidenceUnit(
                unit_id="evidence_" + doc,
                document_id=doc,
                chunk_id="chunk_" + doc,
                claim_text="Refrigerate cooked leftovers within 2 hours.",
                evidence=(
                    Support(
                        chunk_id="chunk_" + doc,
                        support_quote="Refrigerate cooked leftovers within 2 hours.",
                    ),
                ),
            ),
        )
        for doc in ("doc_a", "doc_b")
    }
    return GenerationInput(
        question="How should I store leftovers?",
        corpus_snapshot_id="snapshot_fixture",
        evidence_groups=units,
        source_applicability={},
        instructions="Use approved evidence only.",
        output_schema={},
    )


def generator_for(tmp_path, handler, *, settings=None, api_key="test-secret", clock=None):
    settings = settings or GroqSettings()
    ledger = UsageLedger(tmp_path / "groq.sqlite", settings, **({"clock": clock} if clock else {}))
    generator = GroqGenerator(
        "openai/gpt-oss-120b",
        settings,
        ledger,
        api_key=api_key,
        transport=httpx.MockTransport(handler),
    )
    return generator, ledger


def success(request):
    return httpx.Response(
        200,
        json={
            "choices": [
                {"finish_reason": "stop", "message": {"content": '{"unit_ids":["u1","u0"]}'}}
            ],
            "usage": {"total_tokens": 120},
        },
    )


def test_compact_protocol_restores_exact_claims_and_records_actual_usage(tmp_path, generation):
    observed = []

    def handle(request):
        observed.append(request)
        return success(request)

    generator, ledger = generator_for(tmp_path, handle)
    draft = generator.generate(generation)
    assert [d.document_id for d in draft.document_answers] == ["doc_b", "doc_a"]
    for document in draft.document_answers:
        unit = generation.evidence_groups[document.document_id][0]
        assert document.claims[0].claim_text == unit.claim_text
        assert document.claims[0].evidence == unit.evidence
    body = json.loads(observed[0].content)
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["max_completion_tokens"] == 1024
    assert body["reasoning_effort"] == "low" and body["include_reasoning"] is False
    assert body["response_format"]["json_schema"]["strict"] is True
    assert "tools" not in body and body["stream"] is False
    assert "support_quote" not in observed[0].content.decode()
    assert "test-secret" not in observed[0].content.decode()
    assert ledger.summary()["windows"]["day"]["tokens"] == 120
    assert ledger.summary()["windows"]["day"]["pending_calls"] == 0
    with sqlite3.connect(ledger.path) as connection:
        assert "secret" not in str(connection.execute("SELECT * FROM calls").fetchall())


@pytest.mark.parametrize(
    "content",
    [
        '{"unit_ids":["u0","unknown"]}',
        '{"unit_ids":["u0","u0"]}',
        '{"unit_ids":["u0"]}',
        '{"unit_ids":["u0","u1"],"claim":"invented"}',
        '{"unit_ids":[1,2]}',
        "not-json",
    ],
)
def test_unknown_duplicate_missing_or_extra_output_is_rejected(tmp_path, generation, content):
    generator, ledger = generator_for(
        tmp_path,
        lambda request: httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                "usage": {"total_tokens": 150},
            },
        ),
    )
    with pytest.raises(AnswerValidationError):
        generator.generate(generation)
    assert ledger.summary()["windows"]["hour"]["tokens"] == 150


@pytest.mark.parametrize("status", [400, 401, 403, 500, 503, 302])
def test_provider_errors_are_sanitized_and_not_retried(tmp_path, generation, status):
    observed = []

    def handle(request):
        observed.append(request)
        return httpx.Response(status, text="test-secret private exception")

    generator, ledger = generator_for(tmp_path, handle)
    with pytest.raises(AnswerModelUnavailable) as error:
        generator.generate(generation)
    assert "secret" not in str(error.value) and len(observed) == 1
    assert ledger.summary()["windows"]["day"]["tokens"] > 1024


def test_missing_key_and_oversized_input_spend_nothing(tmp_path, generation):
    def forbidden(request):
        pytest.fail("unexpected API call")

    generator, ledger = generator_for(tmp_path, forbidden, api_key=None)
    with pytest.raises(AnswerModelUnavailable):
        generator.generate(generation)
    assert not ledger.path.exists()
    generator, ledger = generator_for(
        tmp_path, forbidden, settings=GroqSettings(max_input_tokens=100)
    )
    with pytest.raises(TokenBudgetExceeded):
        generator.generate(generation)
    assert not ledger.path.exists()


def test_provider_429_persists_cooldown_across_instances(tmp_path, generation):
    observed = []

    def handle(request):
        observed.append(request)
        return httpx.Response(429, headers={"retry-after": "90"})

    generator, ledger = generator_for(tmp_path, handle, clock=lambda: 1000)
    with pytest.raises(UsageLimitExceeded) as error:
        generator.generate(generation)
    assert error.value.retry_after_seconds == 90
    other = UsageLedger(ledger.path, ledger.settings, clock=lambda: 1030)
    with pytest.raises(UsageLimitExceeded) as error:
        other.reserve(1)
    assert error.value.retry_after_seconds == 60 and len(observed) == 1
    assert other.summary()["retry_after_seconds"] == 60


def test_timeout_retains_reservation_and_expired_request_never_calls(tmp_path, generation):
    def timed_out(request):
        raise httpx.ReadTimeout("test-secret")

    generator, ledger = generator_for(tmp_path, timed_out)
    with pytest.raises(TimeoutError):
        generator.generate(generation)
    assert ledger.summary()["windows"]["day"]["tokens"] > 1024
    with request_context(-1), pytest.raises(TimeoutError):
        generator.generate(generation)
    assert ledger.summary()["windows"]["day"]["requests"] == 1


def test_truncated_and_oversized_output_never_authorizes_claims(tmp_path, generation):
    generator, _ = generator_for(
        tmp_path,
        lambda request: httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "length", "message": {"content": '{"unit_ids":["u0","u1"]}'}}
                ],
            },
        ),
    )
    with pytest.raises(AnswerValidationError, match="provider_incomplete"):
        generator.generate(generation)
    generator, _ = generator_for(
        tmp_path, lambda request: httpx.Response(200, content=b"x" * 70000)
    )
    with pytest.raises(AnswerModelUnavailable):
        generator.generate(generation)


@pytest.mark.parametrize(
    "kind,window,duration",
    [
        ("requests", "minute", 60),
        ("requests", "hour", 3600),
        ("requests", "day", 86400),
        ("tokens", "minute", 60),
        ("tokens", "hour", 3600),
        ("tokens", "day", 86400),
    ],
)
def test_each_window_independently_blocks_and_recovers(tmp_path, kind, window, duration):
    limits = {f"requests_per_{name}": 100 for name in ("minute", "hour", "day")}
    limits.update({f"tokens_per_{name}": 10000 for name in ("minute", "hour", "day")})
    # Equal limits satisfy increasing settings; let shorter windows expire between reservations.
    limits[f"{kind}_per_{window}"] = 1 if kind == "requests" else 2000
    if window == "hour":
        limits[f"{kind}_per_minute"] = limits[f"{kind}_per_hour"]
    if window == "day":
        limits[f"{kind}_per_minute"] = limits[f"{kind}_per_day"]
        limits[f"{kind}_per_hour"] = limits[f"{kind}_per_day"]
    limits.update(max_input_tokens=100, max_completion_tokens=100)
    now = [100000.0]
    ledger = UsageLedger(tmp_path / "usage.sqlite", GroqSettings(**limits), clock=lambda: now[0])
    ledger.reserve(1500 if kind == "tokens" else 1)
    now[0] += {"minute": 0, "hour": 61, "day": 3601}[window]
    with pytest.raises(UsageLimitExceeded) as error:
        ledger.reserve(1500 if kind == "tokens" else 1)
    assert error.value.retry_after_seconds == duration - (now[0] - 100000)
    now[0] = 100000 + duration
    ledger.reserve(1500 if kind == "tokens" else 1)


def test_concurrent_reservations_cannot_overspend(tmp_path):
    settings = GroqSettings(requests_per_minute=2, requests_per_hour=2, requests_per_day=2)
    path = tmp_path / "usage.sqlite"
    UsageLedger(path, settings).summary()

    def reserve(_):
        try:
            UsageLedger(path, settings).reserve(10)
            return True
        except UsageLimitExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(reserve, range(12)))
    assert sum(outcomes) == 2
    assert UsageLedger(path, settings).summary()["windows"]["day"]["requests"] == 2


def test_compact_input_does_not_mutate_authoritative_evidence(generation):
    original = generation.model_dump_json()
    mapping, messages, _ = compact_input(generation)
    assert list(mapping) == ["u0", "u1"]
    assert generation.model_dump_json() == original
    assert json.loads(messages[1]["content"])["evidence"][0]["id"] == "u0"


def test_total_provider_deadline_cancels_slow_body_and_retains_usage(tmp_path, generation):
    import time

    import anyio

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"choices":'
            await anyio.sleep(1)
            yield b"[]}"

    generator, ledger = generator_for(
        tmp_path, lambda request: httpx.Response(200, stream=SlowBody())
    )
    generator.timeout_seconds = 0.02
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        generator.generate(generation)
    assert time.monotonic() - started < 0.5
    assert ledger.summary()["windows"]["day"]["tokens"] > 1024
    assert ledger.summary()["windows"]["day"]["pending_calls"] == 0
