"""Conditional administrative refresh. Never writes the active corpus or review decisions."""

import fcntl
import json
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

from dietary_rag.corpus.identity import sha256_bytes, stable_id
from dietary_rag.ingestion.acquisition import AcquisitionService
from dietary_rag.ingestion.network import DownloadError


def safe_validator(value):
    return (
        value
        if isinstance(value, str)
        and 0 < len(value) <= 1024
        and all(32 <= ord(c) < 127 for c in value)
        else None
    )


class RefreshService:
    def __init__(self, registry, settings, *, fetcher=None):
        self.registry = registry
        self.acquisition = AcquisitionService(registry, settings, fetcher=fetcher)
        self.directory = settings.data_dir / "staging/refresh"

    def run(self, document_ids, write_report):
        # Serialize cache read/network/write so overlapping admins cannot lose validators.
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / "refresh.lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                path = self.directory / "state.json"
                cache = json.loads(path.read_text()) if path.exists() else {}
                rows = [self.refresh(i, cache) for i in document_ids]
                write_report(path, cache)
                return {
                    "version": "refresh_report_v1",
                    "active_snapshot_modified": False,
                    "results": rows,
                    "requires_review_or_rebuild": any(
                        r["status"] in ("needs_review", "metadata_changed", "blocked") for r in rows
                    ),
                }
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def refresh(self, document_id, cache):
        started = time.monotonic()
        row = {"document_id": document_id, "checked_at": datetime.now(UTC).isoformat()}
        try:
            source = self.registry.get(document_id)
            fingerprint = stable_id("refresh_metadata", {"source": source})
            previous = cache.get(document_id, {})
            metadata_changed = bool(
                previous
                and (
                    previous["metadata_id"] != fingerprint or previous.get("review_required", False)
                )
            )
            if not self.registry.consumer_eligibility(document_id).eligible_for_consumer_ingestion:
                row.update(
                    status="blocked",
                    reason="eligibility_changed_or_unapproved; review and rebuild required",
                )
                return row
            validators = {}
            valid_cache = False
            if previous and not metadata_changed:
                try:
                    artifact = self.acquisition.artifacts.describe(previous["raw_sha256"])
                    valid_cache = (
                        artifact.sha256 == source.access.sha256
                        and artifact.byte_count == source.access.bytes
                    )
                except (OSError, ValueError):
                    pass
            if valid_cache and previous["resolved_url"] == str(source.source_url):
                for key, header in [
                    ("etag", "If-None-Match"),
                    ("last_modified", "If-Modified-Since"),
                ]:
                    if value := safe_validator(previous.get(key)):
                        validators[header] = value
            hosts = frozenset(
                urlsplit(str(u)).hostname for u in (source.source_url, source.resolved_url) if u
            )
            download = self.acquisition.fetcher.fetch(
                str(source.source_url),
                hosts,
                self.acquisition._limit(source),
                validators=validators,
            )
            if download.status_code == 304:
                if (
                    not valid_cache
                    or not validators
                    or download.resolved_url != previous["resolved_url"]
                ):
                    raise DownloadError(
                        "invalid_not_modified", "304 does not match verified cached evidence"
                    )
                # Verify bytes again after the network request; a 304 never repairs a corrupt cache.
                self.acquisition.artifacts.describe(previous["raw_sha256"])
                row.update(status="unchanged", http_status=304, raw_sha256=previous["raw_sha256"])
                cache[document_id] = {**previous, "checked_at": row["checked_at"]}
            else:
                # Reuse existing acquisition inspection/provenance; no duplicate approval path.
                original = self.acquisition.fetcher

                class CachedDownload:
                    def fetch(self, *args, **kwargs):
                        return download

                try:
                    self.acquisition.fetcher = CachedDownload()
                    result = self.acquisition.fetch_source(document_id)
                finally:
                    self.acquisition.fetcher = original
                row.update(
                    status=result.status,
                    http_status=200,
                    acquisition=result.model_dump(mode="json"),
                )
                if result.status == "stored" and metadata_changed:
                    row.update(
                        status="metadata_changed",
                        reason="registry metadata differs; exact-byte extraction reviews and corpus rebuild required",
                    )
                elif result.status == "stored":
                    row["status"] = "unchanged"
                if result.status in ("stored", "needs_review"):
                    cache[document_id] = {
                        "metadata_id": fingerprint,
                        "review_required": metadata_changed or result.status == "needs_review",
                        "raw_sha256": sha256_bytes(download.content),
                        "resolved_url": download.resolved_url,
                        "checked_at": row["checked_at"],
                        "etag": safe_validator(download.etag),
                        "last_modified": safe_validator(download.last_modified),
                    }
        except (DownloadError, OSError, ValueError, KeyError) as exc:
            row.update(status="failed", failure_code=getattr(exc, "code", type(exc).__name__))
        finally:
            row["elapsed_seconds"] = time.monotonic() - started
        return row
