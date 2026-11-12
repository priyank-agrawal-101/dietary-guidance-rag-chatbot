"""Explicit administrative checks and source acquisition; no chat-time fetches."""

import argparse
import json
import os
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from dietary_rag.corpus.registry import load_registry
from dietary_rag.settings import load_settings


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _rooted(path: Path, root: Path) -> Path:
    return path if path.is_absolute() else root / path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dietary-rag")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-settings", help="validate and display nonsecret settings")
    check.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
    check.add_argument("--project-root", type=Path, default=Path.cwd())
    registry_check = commands.add_parser(
        "check-registry", help="validate reviewed consumer candidates"
    )
    registry_check.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
    registry_check.add_argument("--project-root", type=Path, default=Path.cwd())
    registry_check.add_argument(
        "--include-decisions", action="store_true", help="show reasons for every source"
    )
    importer = commands.add_parser(
        "import-source", help="import a reviewed file with its original JSON receipt"
    )
    importer.add_argument("document_id")
    importer.add_argument("--file", type=Path, required=True)
    importer.add_argument("--evidence", type=Path, required=True)
    fetch = commands.add_parser(
        "fetch-sources", help="download explicitly selected reviewed consumer sources"
    )
    fetch.add_argument("document_ids", nargs="*")
    fetch.add_argument("--all-eligible", action="store_true")
    for command in (importer, fetch):
        command.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
        command.add_argument("--project-root", type=Path, default=Path.cwd())
        command.add_argument("--report", type=Path, help="also save an atomic JSON report")
    coverage = commands.add_parser(
        "audit-coverage", help="audit eligible families, PDFs and optional stored acquisitions"
    )
    coverage.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
    coverage.add_argument("--project-root", type=Path, default=Path.cwd())
    coverage.add_argument(
        "--verify-acquisitions",
        action="store_true",
        help="read the existing SQLite catalog and verify stored artifacts",
    )
    coverage.add_argument("--report", type=Path)
    html = commands.add_parser(
        "extract-html", help="extract reviewed acquired HTML into inspectable native blocks"
    )
    html.add_argument("document_ids", nargs="*")
    html.add_argument("--all-eligible", action="store_true")
    html.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
    html.add_argument("--project-root", type=Path, default=Path.cwd())
    html.add_argument("--output-dir", type=Path, default=Path("data/staging/html-extraction"))
    html.add_argument("--report", type=Path)
    pdf = commands.add_parser(
        "extract-pdf", help="extract reviewed native PDF layouts and contextual tables"
    )
    pdf.add_argument("document_ids", nargs="*")
    pdf.add_argument("--all-eligible", action="store_true")
    pdf.add_argument(
        "--comparison",
        action="store_true",
        help="explicit nondefault approved comparison with original file/receipt",
    )
    pdf.add_argument("--file", type=Path)
    pdf.add_argument("--evidence", type=Path)
    pdf.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
    pdf.add_argument("--project-root", type=Path, default=Path.cwd())
    pdf.add_argument("--output-dir", type=Path, default=Path("data/staging/pdf-extraction"))
    pdf.add_argument("--render-dir", type=Path, help="also save original/overlay page PNGs")
    pdf.add_argument("--report", type=Path)
    ocr = commands.add_parser("extract-ocr", help="OCR explicitly reviewed image regions")
    ocr.add_argument("--file", type=Path, required=True)
    ocr.add_argument("--document", type=Path, required=True, help="original DocumentVersion JSON")
    ocr.add_argument("--plan", type=Path, required=True, help="byte-bound reviewed OCRPlan JSON")
    ocr.add_argument("--tesseract", type=Path)
    ocr.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
    ocr.add_argument("--project-root", type=Path, default=Path.cwd())
    ocr.add_argument("--output-dir", type=Path, default=Path("data/staging/ocr-extraction"))
    ocr.add_argument("--report", type=Path)
    normalization = commands.add_parser(
        "normalize-extractions", help="normalize native exports with reviewed exclusion masks"
    )
    normalization.add_argument("inputs", nargs="*", type=Path)
    normalization.add_argument(
        "--reports", nargs="+", type=Path, help="explicit native extraction CLI reports"
    )
    normalization.add_argument(
        "--review-file", type=Path, help="exact normalization review receipts"
    )
    normalization.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
    normalization.add_argument("--project-root", type=Path, default=Path.cwd())
    normalization.add_argument(
        "--output-dir", type=Path, default=Path("data/staging/normalization")
    )
    normalization.add_argument("--report", type=Path)
    from dietary_rag.ingestion.chunk_commands import register_commands

    register_commands(commands)
    from dietary_rag.ingestion.index_commands import register_commands as register_index_commands

    register_index_commands(commands)
    from dietary_rag.ingestion.retrieval_commands import register_commands as register_retrieval

    register_retrieval(commands)
    from dietary_rag.ingestion.lifecycle_commands import register_commands as register_lifecycle

    register_lifecycle(commands)
    from dietary_rag.answering.commands import register_commands as register_answering

    register_answering(commands)
    policy_check = commands.add_parser(
        "check-policy", help="inspect policy routing without retrieval"
    )
    policy_check.add_argument("question")
    policy_check.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
    policy_check.add_argument("--project-root", type=Path, default=Path.cwd())
    serve = commands.add_parser("serve", help="serve the validated chat API")
    serve.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
    serve.add_argument("--project-root", type=Path, default=Path.cwd())
    serve.add_argument("--env-file", type=Path)
    serve.add_argument("--offline", action="store_true")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    try:
        environ = os.environ
        if args.command in ("answer-question", "serve"):
            from dietary_rag.api.runtime import load_environment

            environ = load_environment(args.project_root, args.env_file)
        settings = load_settings(args.config, project_root=args.project_root, environ=environ)
        if args.command == "serve":
            import uvicorn

            from dietary_rag.api.app import create_app

            app = create_app(
                project_root=args.project_root,
                settings=settings,
                environ=environ,
                offline=args.offline,
            )
            uvicorn.run(app, host=args.host, port=args.port, log_level="warning", access_log=False)
            return 0
        if args.command == "check-policy":
            from dietary_rag.api.contracts import ChatRequest
            from dietary_rag.policy.service import PolicyService
            from dietary_rag.policy.templates import MESSAGES

            request = ChatRequest.model_validate(
                {"question": args.question}, context={"runtime": settings.runtime}
            )
            decision = PolicyService(settings.policy).decide(request.question)
            print(
                json.dumps(
                    {
                        "decision": decision.model_dump(mode="json"),
                        "message": MESSAGES[decision.template] if decision.template else None,
                        "searched_documents": [],
                        "citations": [],
                    },
                    indent=2,
                )
            )
            return 0
        if args.command != "check-settings":
            registry = load_registry(settings.source_registry)
        if args.command == "answer-question":
            from dietary_rag.answering.commands import run_command

            return run_command(args, settings, registry, _write_report)
        if args.command in ("prepare-tokenizer", "chunk-sources", "compare-chunks"):
            from dietary_rag.ingestion.chunk_commands import run_command

            return run_command(args, settings, registry, _write_report)
        if args.command in ("prepare-embeddings", "build-index", "validate-index"):
            from dietary_rag.ingestion.index_commands import run_command

            return run_command(args, settings, registry, _write_report)
        if args.command in ("search-corpus", "evaluate-retrieval"):
            from dietary_rag.ingestion.retrieval_commands import run_command

            return run_command(args, settings, registry, _write_report)
        if args.command in ("activate-corpus", "rollback-corpus", "corpus-status"):
            from dietary_rag.ingestion.lifecycle_commands import run_command

            return run_command(args, settings, registry, _write_report)
        if args.command == "normalize-extractions":
            from dietary_rag.corpus.identity import sha256_bytes
            from dietary_rag.ingestion.normalization import Normalizer, parse_extraction
            from dietary_rag.ingestion.normalization_contracts import NormalizationReview

            if bool(args.inputs) == bool(args.reports):
                raise ValueError("Choose explicit extraction files OR --reports")
            paths = [_rooted(p, args.project_root) for p in args.inputs]
            for path in args.reports or ():
                native_report = json.loads(_rooted(path, args.project_root).read_text())
                for row in native_report.get("results", [native_report]):
                    if "output" not in row:
                        raise ValueError(
                            "Native report has no extraction artifact for a selected input"
                        )
                    paths.append(_rooted(Path(row["output"]), args.project_root))
            paths = list(dict.fromkeys(paths))
            if not paths or len(paths) > 32:
                raise ValueError("Normalization requires 1–32 explicit extraction artifacts")
            receipts = {}
            if args.review_file:
                for value in json.loads(_rooted(args.review_file, args.project_root).read_text())[
                    "reviews"
                ]:
                    receipt = NormalizationReview.model_validate(value)
                    if receipt.original_extraction_id in receipts:
                        raise ValueError("Duplicate normalization review receipt")
                    receipts[receipt.original_extraction_id] = receipt
            results = []
            normalizer = Normalizer(registry)
            used = set()
            for path in paths:
                try:
                    if path.stat().st_size > 20_000_000:
                        raise ValueError("Native normalization artifact exceeds 20 MB")
                    content = path.read_bytes()
                    native = parse_extraction(json.loads(content))
                    receipt = receipts.get(native.extraction_id)
                    if receipt:
                        used.add(native.extraction_id)
                    normalized = normalizer.normalize(
                        native, artifact_sha256=sha256_bytes(content), review=receipt
                    )
                    output = (
                        _rooted(args.output_dir, args.project_root)
                        / f"{normalized.normalization_id}.json"
                    )
                    if receipt:
                        output = output.with_name(f"{normalized.normalization_id}-reviewed.json")
                    _write_report(output, normalized.model_dump(mode="json"))
                    results.append(
                        {
                            "document_id": native.document.document_id,
                            "status": normalized.review_state,
                            "normalization_id": normalized.normalization_id,
                            "original_extraction_id": native.extraction_id,
                            "input": str(path),
                            "blocks": len(normalized.blocks),
                            "tables": len(normalized.tables),
                            "excluded_blocks": sum(
                                d.state == "excluded" for d in normalized.decisions
                            ),
                            "quarantined_blocks": sum(
                                d.state == "quarantined" for d in normalized.decisions
                            ),
                            "transformations": len(normalized.transformations),
                            "comparison_only": normalized.comparison_only,
                            "fixture_kind": normalized.fixture_kind,
                            "output": str(output),
                        }
                    )
                except (ValueError, OSError) as exc:
                    results.append({"input": str(path), "status": "failed", "error": str(exc)})
            if receipts.keys() - used:
                raise ValueError("Review file contains receipts outside the selected inputs")
            report = {"status": "normalization_only_no_activation", "results": results}
            if args.report:
                _write_report(_rooted(args.report, args.project_root), report)
            print(json.dumps(report, indent=2))
            return 0 if all(r["status"] in ("pending", "reviewed") for r in results) else 1
        if args.command == "extract-ocr":
            from dietary_rag.corpus.contracts import DocumentVersion
            from dietary_rag.ingestion.ocr import OCRExtractor
            from dietary_rag.ingestion.ocr_contracts import OCRPlan

            document = DocumentVersion.model_validate_json(
                _rooted(args.document, args.project_root).read_text()
            )
            plan = OCRPlan.model_validate_json(_rooted(args.plan, args.project_root).read_text())
            if plan.fixture_kind == "original_pdf":
                from dietary_rag.ingestion.pdf import PDFExtractor

                # Current registry metadata/exclusions and reviewed original bytes still apply.
                reviewed = PDFExtractor(registry, document.document_id)
                if document.raw_sha256 != reviewed.source.access.sha256:
                    raise ValueError("OCR original document differs from current source review")
                fields = (
                    "document_name",
                    "family_id",
                    "publisher",
                    "source_url",
                    "dates",
                    "rights_review",
                    "content_role",
                    "target_audience",
                    "jurisdiction",
                )
                if any(
                    getattr(document, f) != getattr(reviewed.source, f) for f in fields
                ) or document.eligibility.model_dump() != reviewed.source.selection.model_dump(
                    exclude={"approved_pdf_for_milestone"}
                ):
                    raise ValueError("OCR original document metadata differs from source review")
            extraction = OCRExtractor(plan, tesseract=args.tesseract).extract_report(
                _rooted(args.file, args.project_root),
                document,
                output_dir=_rooted(args.output_dir, args.project_root),
            )
            output = (
                _rooted(args.output_dir, args.project_root) / f"{extraction.extraction_id}.json"
            )
            _write_report(output, extraction.model_dump(mode="json"))
            report = {
                "status": "quarantined"
                if extraction.review_state == "quarantined"
                else "ocr_pending_review",
                "extraction_id": extraction.extraction_id,
                "fixture_kind": extraction.fixture_kind,
                "blocks": len(extraction.blocks),
                "quarantined_candidates": sum(
                    c.state == "quarantined" for c in extraction.candidates
                ),
                "ocr_regions": len(extraction.images),
                "output": str(output),
            }
            if args.report:
                _write_report(_rooted(args.report, args.project_root), report)
            print(json.dumps(report, indent=2))
            return 1 if extraction.review_state == "quarantined" else 0
        if args.command == "extract-pdf":
            from dietary_rag.corpus.artifacts import ArtifactStore
            from dietary_rag.corpus.identity import stable_id
            from dietary_rag.corpus.repository import CorpusRepository
            from dietary_rag.ingestion.pdf import PDFExtractor

            if bool(args.document_ids) == args.all_eligible:
                raise ValueError("Choose explicit document IDs OR --all-eligible")
            if args.comparison != bool(args.file and args.evidence) or bool(args.file) != bool(
                args.evidence
            ):
                raise ValueError("Comparisons require --comparison, --file and --evidence together")
            if args.comparison and (args.all_eligible or len(args.document_ids) != 1):
                raise ValueError("Choose exactly one explicit approved comparison document")
            ids = (
                list(dict.fromkeys(args.document_ids))
                if args.document_ids
                else [
                    source.document_id
                    for source in registry.consumer_sources()
                    if source.format == "pdf"
                ]
            )
            extractors = [
                PDFExtractor(registry, document_id, comparison=args.comparison)
                for document_id in ids
            ]
            artifacts = ArtifactStore(settings.data_dir / "staging/raw")
            snapshot = stable_id("registry", {"registry": registry})
            results = []
            for extractor in extractors:
                try:
                    if args.comparison:
                        raw = _rooted(args.file, args.project_root)
                        extraction = extractor.extract_reviewed_file(
                            raw, _rooted(args.evidence, args.project_root)
                        )
                    else:
                        with CorpusRepository(
                            settings.data_dir / "staging/provenance.sqlite",
                            artifacts,
                            read_only=True,
                        ) as repository:
                            versions = repository.document_versions(
                                extractor.source.document_id, registry_snapshot_id=snapshot
                            )
                            if not versions:
                                raise ValueError(
                                    "no acquisition for the current reviewed registry snapshot"
                                )
                            document = max(
                                versions, key=lambda v: (v.retrieved_at, v.document_version_id)
                            )
                            raw = (
                                artifacts.root
                                / artifacts.describe(document.raw_sha256).relative_path
                            )
                            extraction = extractor.extract_report(raw, document)
                    rendered = []
                    if args.render_dir:
                        from dietary_rag.ingestion.pdf_review import render_pdf_review

                        rendered = render_pdf_review(
                            raw, extraction, _rooted(args.render_dir, args.project_root)
                        )
                    output = (
                        _rooted(args.output_dir, args.project_root)
                        / f"{extraction.extraction_id}.json"
                    )
                    _write_report(output, extraction.model_dump(mode="json"))
                    results.append(
                        {
                            "document_id": extractor.source.document_id,
                            "status": "quarantined"
                            if extraction.review_state == "quarantined"
                            else "extracted_pending_review",
                            "comparison_only": extraction.comparison_only,
                            "document_version_id": extraction.document.document_version_id,
                            "version_selection": "verified_original_receipt"
                            if args.comparison
                            else "latest_current_reviewed_retrieval",
                            "extraction_id": extraction.extraction_id,
                            "blocks": len(extraction.blocks),
                            "tables": len(extraction.tables),
                            "pages": len(extraction.pages),
                            "output": str(output),
                            "renders": rendered,
                        }
                    )
                except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                    results.append(
                        {
                            "document_id": extractor.source.document_id,
                            "status": "failed",
                            "error": str(exc),
                        }
                    )
            report = {"status": "native_pdf_only_pending_review", "results": results}
            if args.report:
                _write_report(_rooted(args.report, args.project_root), report)
            print(json.dumps(report, indent=2))
            return 0 if all(r["status"] == "extracted_pending_review" for r in results) else 1
        if args.command == "extract-html":
            from dietary_rag.corpus.artifacts import ArtifactStore
            from dietary_rag.corpus.identity import stable_id
            from dietary_rag.corpus.repository import CorpusRepository
            from dietary_rag.ingestion.html import HTMLExtractor

            if bool(args.document_ids) == args.all_eligible:
                raise ValueError("Choose explicit document IDs OR --all-eligible")
            ids = (
                list(dict.fromkeys(args.document_ids))
                if args.document_ids
                else [s.document_id for s in registry.consumer_sources() if s.format == "html"]
            )
            extractors = [HTMLExtractor(registry, document_id) for document_id in ids]
            artifacts = ArtifactStore(settings.data_dir / "staging/raw")
            snapshot_id = stable_id("registry", {"registry": registry})
            results = []
            with CorpusRepository(
                settings.data_dir / "staging/provenance.sqlite", artifacts, read_only=True
            ) as repository:
                for extractor in extractors:
                    document_id = extractor.source.document_id
                    try:
                        versions = repository.document_versions(
                            document_id, registry_snapshot_id=snapshot_id
                        )
                        if not versions:
                            raise ValueError(
                                "no acquisition for the current reviewed registry snapshot"
                            )
                        document = max(
                            versions, key=lambda v: (v.retrieved_at, v.document_version_id)
                        )
                        artifact = artifacts.describe(document.raw_sha256)
                        extraction = extractor.extract_report(
                            artifacts.root / artifact.relative_path, document
                        )
                        output = (
                            _rooted(args.output_dir, args.project_root)
                            / f"{extraction.extraction_id}.json"
                        )
                        _write_report(output, extraction.model_dump(mode="json"))
                        results.append(
                            {
                                "document_id": document_id,
                                "status": "extracted_pending_review",
                                "document_version_id": document.document_version_id,
                                "version_selection": "latest_current_reviewed_retrieval",
                                "blocks": len(extraction.blocks),
                                "tables": len(extraction.tables),
                                "extraction_id": extraction.extraction_id,
                                "output": str(output),
                            }
                        )
                    except (OSError, ValueError) as exc:
                        results.append(
                            {"document_id": document_id, "status": "failed", "error": str(exc)}
                        )
            report = {"status": "native_html_only_pending_review", "results": results}
            if args.report:
                _write_report(_rooted(args.report, args.project_root), report)
            print(json.dumps(report, indent=2))
            return 0 if all(r["status"] == "extracted_pending_review" for r in results) else 1
        if args.command == "audit-coverage":
            from dietary_rag.corpus.artifacts import ArtifactStore
            from dietary_rag.corpus.coverage import audit_coverage
            from dietary_rag.corpus.repository import CorpusRepository

            if args.verify_acquisitions:
                database = settings.data_dir / "staging/provenance.sqlite"
                artifacts = ArtifactStore(settings.data_dir / "staging/raw")
                with CorpusRepository(database, artifacts, read_only=True) as repository:
                    report = audit_coverage(registry, repository=repository)
            else:
                report = audit_coverage(registry)
            if args.report:
                _write_report(_rooted(args.report, args.project_root), report)
            print(json.dumps(report, indent=2))
            passed = report["family_target_met"] and report["approved_pdf_registry_target_met"]
            if args.verify_acquisitions:
                passed = (
                    passed
                    and report["consumer_acquisitions"]["minimum_pdf_acquisitions_met"]
                    and not report["consumer_acquisitions"]["missing_or_invalid"]
                )
            return 0 if passed else 1
        if args.command in ("import-source", "fetch-sources"):
            from dietary_rag.ingestion.acquisition import AcquisitionService

            if args.command == "import-source":
                ids = [args.document_id]
            else:
                if bool(args.document_ids) == args.all_eligible:
                    raise ValueError("Choose explicit document IDs OR --all-eligible")
                ids = (
                    list(dict.fromkeys(args.document_ids))
                    if args.document_ids
                    else [s.document_id for s in registry.consumer_sources()]
                )
            for document_id in ids:
                registry.get(
                    document_id
                )  # Validate the entire selection before any writes/network.
            service = AcquisitionService(registry, settings)
            results = (
                [
                    service.import_file(
                        ids[0],
                        _rooted(args.file, args.project_root),
                        _rooted(args.evidence, args.project_root),
                    )
                ]
                if args.command == "import-source"
                else [service.fetch_source(document_id) for document_id in ids]
            )
            report = {
                "status": "acquisition_only_extraction_pending",
                "counts": dict(Counter(result.status for result in results)),
                "database_path": str(service.database_path),
                "artifact_root": str(service.artifacts.root),
                "results": [result.model_dump(mode="json") for result in results],
            }
            if args.report:
                _write_report(_rooted(args.report, args.project_root), report)
            print(json.dumps(report, indent=2))
            return 0 if all(result.status == "stored" for result in results) else 1
    except ValidationError as exc:
        for error in exc.errors(include_input=False, include_url=False):
            print(f"Invalid setting {'.'.join(map(str, error['loc']))}: {error['msg']}")
        return 2
    except ImportError as exc:
        print(f"Optional dependency unavailable: {exc}; install the required dependency lock")
        return 2
    except (OSError, ValueError, KeyError, RuntimeError, sqlite3.Error) as exc:
        print(f"Cannot complete command: {exc}")
        return 2
    if args.command == "check-settings":
        report = settings.model_dump(mode="json")
    else:
        candidates = registry.consumer_sources()
        report = {
            "status": "reviewed_ingestion_candidates_only",
            "review_date": registry.review_date.isoformat(),
            "candidate_count": len(registry.sources),
            "preserved_existing_source_count": len(registry.policy.existing_document_ids),
            "consumer_ingestion_candidate_count": len(candidates),
            "consumer_family_count": len({source.family_id for source in candidates}),
            "consumer_document_ids": [source.document_id for source in candidates],
            "remaining_activation_gates": registry.policy.activation_gates,
        }
        if args.include_decisions:
            report["decisions"] = [
                registry.consumer_eligibility(source.document_id).model_dump(mode="json")
                for source in registry.sources
            ]
    print(json.dumps(report, indent=2))
    return 0
