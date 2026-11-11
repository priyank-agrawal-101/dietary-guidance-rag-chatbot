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
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config, project_root=args.project_root, environ=os.environ)
        if args.command != "check-settings":
            registry = load_registry(settings.source_registry)
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
