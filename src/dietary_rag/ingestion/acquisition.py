"""Import/fetch orchestration; verified raw documents are not yet extracted guidance."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, AwareDatetime, BaseModel, ConfigDict

from dietary_rag.corpus.artifacts import ArtifactStore
from dietary_rag.corpus.contracts import (
    AcquisitionRecord,
    Contract,
    DocumentVersion,
    Identifier,
    PositiveInt,
    Sha256,
    SourceEligibility,
    SourceFormat,
)
from dietary_rag.corpus.identity import sha256_bytes, stable_id
from dietary_rag.corpus.registry import SourceRegistry
from dietary_rag.corpus.repository import CorpusRepository
from dietary_rag.ingestion.inspection import FileInspection, InspectionError, inspect_file
from dietary_rag.ingestion.network import DownloadError, Fetcher
from dietary_rag.settings import Settings


class SourceEvidence(BaseModel):
    # Historical review receipts contain optional article excerpts/discovery metadata.
    model_config = ConfigDict(extra="ignore", frozen=True)
    source_id: Identifier
    requested_url: AnyHttpUrl
    final_url: AnyHttpUrl
    checked_at: AwareDatetime
    retrieved_at: AwareDatetime | None = None
    status: int
    format: SourceFormat
    bytes: PositiveInt
    sha256: Sha256
    content_type: str | None = None
    raw_path: Path | None = None


class AcquisitionResult(Contract):
    document_id: str
    operation: Literal["import", "fetch"]
    status: Literal["stored", "needs_review", "failed", "blocked"]
    processed_at: AwareDatetime
    acquisition_id: str | None = None
    acquisition: AcquisitionRecord | None = None
    document_version_id: str | None = None
    registry_snapshot_id: str | None = None
    failure_code: str | None = None
    message: str
    inspection: FileInspection | None = None
    reasons: tuple[str, ...] = ()
    input_path: str | None = None
    evidence_path: str | None = None
    attempts: int = 0


class AcquisitionService:
    def __init__(self, registry: SourceRegistry, settings: Settings, *, fetcher=None, clock=None):
        self.registry = registry
        self.settings = settings
        self.artifacts = ArtifactStore(settings.data_dir / "staging/raw")
        self.database_path = settings.data_dir / "staging/provenance.sqlite"
        self.fetcher = fetcher or Fetcher(settings.fetch)
        self.clock = clock or (lambda: datetime.now(UTC))

    def _blocked(self, document_id, operation):
        decision = self.registry.consumer_eligibility(document_id)
        if not decision.eligible_for_consumer_ingestion:
            return AcquisitionResult(
                document_id=document_id,
                operation=operation,
                status="blocked",
                processed_at=self.clock(),
                message="Source is not a reviewed consumer ingestion candidate",
                reasons=decision.reasons,
            )
        return None

    def _save(
        self,
        source,
        record,
        inspection,
        operation,
        *,
        input_path=None,
        evidence_path=None,
        attempts=0,
    ):
        with CorpusRepository(self.database_path, self.artifacts) as repository:
            snapshot_id = repository.save_registry(self.registry)
            acquisition_id = repository.save_acquisition(record)
            reasons = []
            if record.raw_sha256 != source.access.sha256:
                reasons.append("source_bytes_changed_since_review")
            if str(record.resolved_url) != str(source.resolved_url):
                reasons.append("resolved_url_changed_since_review")
            if source.pdf and (
                inspection.page_count != source.pdf.page_count
                or inspection.text_classification != source.pdf.text_classification
            ):
                reasons.append("pdf_inspection_changed_since_review")
            version_id = None
            if not reasons:
                fields = {
                    field: getattr(source, field)
                    for field in (
                        "document_id",
                        "family_id",
                        "document_name",
                        "publisher",
                        "source_url",
                        "format",
                        "dates",
                        "source_tier",
                        "jurisdiction",
                        "target_audience",
                        "intended_audience",
                        "content_role",
                        "rights_review",
                        "author",
                        "medical_reviewer",
                    )
                }
                fields.update(
                    resolved_url=record.resolved_url,
                    retrieved_at=record.retrieved_at,
                    raw_sha256=record.raw_sha256,
                    page_count=inspection.page_count,
                    text_classification=inspection.text_classification,
                    eligibility=SourceEligibility.model_validate(
                        source.selection.model_dump(exclude={"approved_pdf_for_milestone"})
                    ),
                )
                version_id = stable_id(
                    "docv",
                    {
                        "document": fields,
                        "acquisition_id": acquisition_id,
                        "registry_snapshot_id": snapshot_id,
                        "inspection_version": "1",
                    },
                )
                document = DocumentVersion(document_version_id=version_id, **fields)
                repository.save_document_version(
                    document, acquisition_id=acquisition_id, registry_snapshot_id=snapshot_id
                )
        return AcquisitionResult(
            document_id=source.document_id,
            operation=operation,
            processed_at=self.clock(),
            status="needs_review" if reasons else "stored",
            acquisition_id=acquisition_id,
            acquisition=record,
            document_version_id=version_id,
            registry_snapshot_id=snapshot_id,
            inspection=inspection,
            reasons=tuple(reasons),
            message="New bytes/metadata saved for review; no approved version created"
            if reasons
            else "Verified original file and document provenance stored; extraction remains pending",
            input_path=str(input_path) if input_path else None,
            evidence_path=str(evidence_path) if evidence_path else None,
            attempts=attempts,
        )

    def _failure(
        self,
        source,
        operation,
        code,
        message,
        *,
        checked_at=None,
        detected_format=None,
        status_code=None,
        resolved_url=None,
        content_type=None,
        input_path=None,
        evidence_path=None,
    ):
        outcome = (
            "http_error"
            if code == "http_error"
            else (
                "network_error"
                if code
                in ("network_error", "request_timeout", "unsafe_destination", "redirect_limit")
                else ("unexpected_format" if code == "format_mismatch" else "invalid_document")
            )
        )
        record = AcquisitionRecord(
            document_id=source.document_id,
            requested_url=source.source_url,
            resolved_url=resolved_url,
            expected_format=source.format,
            detected_format=detected_format,
            outcome=outcome,
            checked_at=checked_at or self.clock(),
            status_code=status_code,
            content_type=content_type,
            error=f"{code}: {message}",
        )
        with CorpusRepository(self.database_path, self.artifacts) as repository:
            snapshot_id = repository.save_registry(self.registry)
            acquisition_id = repository.save_acquisition(record)
        return AcquisitionResult(
            document_id=source.document_id,
            operation=operation,
            status="failed",
            processed_at=self.clock(),
            acquisition_id=acquisition_id,
            acquisition=record,
            registry_snapshot_id=snapshot_id,
            failure_code=code,
            message=message,
            input_path=str(input_path) if input_path else None,
            evidence_path=str(evidence_path) if evidence_path else None,
        )

    def _limit(self, source):
        return (
            self.settings.fetch.max_pdf_bytes
            if source.format == "pdf"
            else self.settings.fetch.max_html_bytes
        )

    def import_file(
        self, document_id: str, input_path: Path, evidence_path: Path
    ) -> AcquisitionResult:
        if blocked := self._blocked(document_id, "import"):
            return blocked
        source = self.registry.get(document_id)
        try:
            evidence = SourceEvidence.model_validate_json(evidence_path.read_text(encoding="utf-8"))
            if (
                evidence.source_id != document_id
                or evidence.requested_url != source.source_url
                or evidence.final_url != source.resolved_url
                or evidence.status != 200
                or evidence.format != source.format
            ):
                raise InspectionError(
                    "invalid_provenance",
                    "Receipt ID, URL, status or format does not match the reviewed source",
                )
            if evidence.retrieved_at and evidence.retrieved_at > evidence.checked_at:
                raise InspectionError(
                    "invalid_provenance", "Receipt retrieval time is later than its check time"
                )
            with input_path.open("rb") as stream:
                content = stream.read(self._limit(source) + 1)
            if len(content) > self._limit(source):
                raise InspectionError("size_limit", "Saved file exceeds configured byte limit")
            if len(content) != evidence.bytes or sha256_bytes(content) != evidence.sha256:
                raise InspectionError(
                    "receipt_hash_mismatch", "Saved bytes differ from the original receipt"
                )
            inspection = inspect_file(content, source.format, evidence.content_type)
        except InspectionError as exc:
            return self._failure(
                source,
                "import",
                exc.code,
                str(exc),
                detected_format=exc.detected_format,
                input_path=input_path,
                evidence_path=evidence_path,
            )
        except (OSError, ValueError) as exc:
            return self._failure(
                source,
                "import",
                "invalid_provenance",
                f"Could not read a valid saved file/receipt: {type(exc).__name__}",
                input_path=input_path,
                evidence_path=evidence_path,
            )
        artifact = self.artifacts.put_bytes(content)
        # Preserve the receipt's original download/check instant, not today's copy time.
        record = AcquisitionRecord(
            document_id=document_id,
            requested_url=evidence.requested_url,
            resolved_url=evidence.final_url,
            expected_format=source.format,
            detected_format=inspection.detected_format,
            outcome="success",
            checked_at=evidence.checked_at,
            retrieved_at=evidence.retrieved_at or evidence.checked_at,
            status_code=200,
            content_type=evidence.content_type,
            byte_count=artifact.byte_count,
            raw_sha256=artifact.sha256,
            raw_path=artifact.relative_path,
        )
        return self._save(
            source, record, inspection, "import", input_path=input_path, evidence_path=evidence_path
        )

    def fetch_source(self, document_id: str) -> AcquisitionResult:
        if blocked := self._blocked(document_id, "fetch"):
            return blocked
        source = self.registry.get(document_id)
        from urllib.parse import urlsplit

        hosts = frozenset(
            urlsplit(str(url)).hostname for url in (source.source_url, source.resolved_url) if url
        )
        try:
            download = self.fetcher.fetch(str(source.source_url), hosts, self._limit(source))
            inspection = inspect_file(download.content, source.format, download.content_type)
        except DownloadError as exc:
            return self._failure(
                source,
                "fetch",
                exc.code,
                str(exc),
                status_code=exc.status_code,
                resolved_url=exc.resolved_url,
                content_type=exc.content_type,
            )
        except InspectionError as exc:
            return self._failure(
                source,
                "fetch",
                exc.code,
                str(exc),
                detected_format=exc.detected_format,
                status_code=200,
                resolved_url=download.resolved_url,
                content_type=download.content_type,
            )
        artifact = self.artifacts.put_bytes(download.content)
        acquired_at = self.clock()
        record = AcquisitionRecord(
            document_id=document_id,
            requested_url=source.source_url,
            resolved_url=download.resolved_url,
            expected_format=source.format,
            detected_format=inspection.detected_format,
            outcome="success",
            checked_at=acquired_at,
            retrieved_at=acquired_at,
            status_code=200,
            content_type=download.content_type,
            byte_count=artifact.byte_count,
            raw_sha256=artifact.sha256,
            raw_path=artifact.relative_path,
        )
        return self._save(source, record, inspection, "fetch", attempts=download.attempts)
