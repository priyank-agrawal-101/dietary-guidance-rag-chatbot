"""Conditional cache, changed bytes and failure isolation using bounded mock HTTP."""

import json

import httpx

from dietary_rag.cli import _write_report
from dietary_rag.corpus.identity import sha256_bytes
from dietary_rag.corpus.registry import SourceRegistry
from dietary_rag.ingestion.network import Fetcher
from dietary_rag.operations.refresh import RefreshService
from dietary_rag.settings import Settings

HTML = b"<html><main>" + b"Synthetic fixture food storage advice. " * 12 + b"</main></html>"


def prepared(tmp_path, registry, handler):
    values = registry.model_dump()
    for source in values["sources"]:
        if source["document_id"] == "mn_cold_storage":
            source["access"].update(sha256=sha256_bytes(HTML), bytes=len(HTML))
    registry = SourceRegistry.model_validate(values)
    settings = Settings(data_dir=tmp_path / "data")

    def stream(request):
        response = handler(request)
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            stream=httpx.ByteStream(response.content),
        )

    return RefreshService(
        registry,
        settings,
        fetcher=Fetcher(
            settings.fetch, transport=httpx.MockTransport(stream), sleep=lambda _: None
        ),
    )


def test_conditional_304_and_failed_refresh_preserve_active(tmp_path, reviewed_registry):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, headers={"etag": '"v1"'}, content=HTML)
        if len(calls) == 2:
            assert request.headers["if-none-match"] == '"v1"'
            return httpx.Response(304)
        return httpx.Response(503)

    service = prepared(tmp_path, reviewed_registry, handler)
    active = tmp_path / "data/active/active.json"
    active.parent.mkdir(parents=True)
    active.write_text("retained pointer fixture")
    first = service.run(["mn_cold_storage"], _write_report)
    assert first["results"][0]["status"] == "unchanged"
    cache = service.directory / "state.json"
    second = service.run(["mn_cold_storage"], _write_report)
    assert second["results"][0]["http_status"] == 304
    before = cache.read_bytes()
    assert service.run(["mn_cold_storage"], _write_report)["results"][0]["status"] == "failed"
    assert cache.read_bytes() == before
    assert active.read_text() == "retained pointer fixture"


def test_changed_bytes_are_staged_not_approved(tmp_path, reviewed_registry):
    service = prepared(
        tmp_path, reviewed_registry, lambda _: httpx.Response(200, content=HTML + b" changed")
    )
    row = service.run(["mn_cold_storage"], _write_report)["results"][0]
    assert row["status"] == "needs_review"
    assert row["acquisition"]["document_version_id"] is None
    assert not (tmp_path / "data/active").exists()


def test_metadata_change_disables_conditional_headers(tmp_path, reviewed_registry):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, headers={"etag": '"v1"'}, content=HTML)

    service = prepared(tmp_path, reviewed_registry, handler)
    service.run(["mn_cold_storage"], _write_report)
    values = service.registry.model_dump()
    for source in values["sources"]:
        if source["document_id"] == "mn_cold_storage":
            source["document_name"] += " revised"
    service.registry = SourceRegistry.model_validate(values)
    service.acquisition.registry = service.registry
    row = service.run(["mn_cold_storage"], _write_report)["results"][0]
    assert row["status"] == "metadata_changed"
    assert "if-none-match" not in calls[-1].headers
    assert json.loads((service.directory / "state.json").read_text())["mn_cold_storage"][
        "metadata_id"
    ]


def test_pending_metadata_review_survives_repeat_refresh(tmp_path, reviewed_registry):
    service = prepared(tmp_path, reviewed_registry, lambda _: httpx.Response(200, content=HTML))
    service.run(["mn_cold_storage"], _write_report)
    state_path = service.directory / "state.json"
    state = json.loads(state_path.read_text())
    state["mn_cold_storage"]["review_required"] = True
    _write_report(state_path, state)
    assert (
        service.run(["mn_cold_storage"], _write_report)["results"][0]["status"]
        == "metadata_changed"
    )
    assert (
        service.run(["mn_cold_storage"], _write_report)["results"][0]["status"]
        == "metadata_changed"
    )


def test_corrupt_cached_bytes_disable_validators_and_never_repair_approval(
    tmp_path, reviewed_registry
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, headers={"etag": '"v1"'}, content=HTML)

    service = prepared(tmp_path, reviewed_registry, handler)
    service.run(["mn_cold_storage"], _write_report)
    digest = sha256_bytes(HTML)
    artifact = service.acquisition.artifacts._path(digest)
    artifact.write_bytes(b"corrupted")
    result = service.run(["mn_cold_storage"], _write_report)
    assert "if-none-match" not in calls[-1].headers
    assert result["results"][0]["status"] == "failed"
    assert artifact.read_bytes() == b"corrupted"
