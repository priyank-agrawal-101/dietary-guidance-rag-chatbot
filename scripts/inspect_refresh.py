"""Review conditional refresh over actual approved bytes with deterministic HTTP responses."""

import json
import tempfile
from pathlib import Path

import httpx

from dietary_rag.cli import _write_report
from dietary_rag.corpus.artifacts import ArtifactStore
from dietary_rag.corpus.registry import load_registry
from dietary_rag.ingestion.network import Fetcher
from dietary_rag.operations.refresh import RefreshService
from dietary_rag.settings import load_settings


def main():
    root = Path(__file__).resolve().parents[1]
    settings = load_settings(root / "config/settings.yaml", project_root=root, environ={})
    registry = load_registry(settings.source_registry)
    document = registry.get("mn_cold_storage")
    content = ArtifactStore(settings.data_dir / "staging/raw").read_bytes(document.access.sha256)
    pointer = settings.data_dir / "active/active.json"
    before = pointer.read_bytes()
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                200,
                headers={"etag": '"reviewed-fixture"', "content-type": "text/html"},
                stream=httpx.ByteStream(content),
            )
        if len(calls) == 2:
            assert request.headers["if-none-match"] == '"reviewed-fixture"'
            return httpx.Response(304)
        if len(calls) == 3:
            return httpx.Response(503)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            stream=httpx.ByteStream(content + b"<!-- simulated change -->"),
        )

    with tempfile.TemporaryDirectory(prefix="dietary-refresh-review-") as temporary:
        local = settings.model_copy(update={"data_dir": Path(temporary)})
        fetcher = Fetcher(
            settings.fetch.model_copy(update={"max_retries": 0}),
            transport=httpx.MockTransport(handler),
        )
        service = RefreshService(registry, local, fetcher=fetcher)
        reports = [service.run([document.document_id], _write_report) for _ in range(4)]
    statuses = [r["results"][0]["status"] for r in reports]
    checks = {
        "unchanged_200": statuses[0] == "unchanged",
        "conditional_304": reports[1]["results"][0].get("http_status") == 304,
        "failure_preserves_cache_and_active": statuses[2] == "failed"
        and pointer.read_bytes() == before,
        "changed_bytes_require_review": statuses[3] == "needs_review"
        and reports[3]["results"][0]["acquisition"]["document_version_id"] is None,
    }
    _write_report(
        root / "docs/refresh-review.json",
        {
            "version": "refresh_review_v1",
            "transport": "mock HTTP over real approved original bytes; not live origin freshness",
            "document_id": document.document_id,
            "raw_sha256": document.access.sha256,
            "checks": checks,
            "statuses": statuses,
            "active_pointer_unchanged": pointer.read_bytes() == before,
        },
    )
    print(json.dumps(checks))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
