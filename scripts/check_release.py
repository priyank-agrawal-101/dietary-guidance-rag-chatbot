"""Validate current release evidence and optionally publish a local gated bundle."""

import argparse
import json
from pathlib import Path

from dietary_rag.api.runtime import ActiveRuntime
from dietary_rag.cli import _write_report
from dietary_rag.corpus.registry import load_registry
from dietary_rag.operations.release import implementation_hash, publish_bundle, release_checks
from dietary_rag.settings import load_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("config/evaluation-cases.json"))
    parser.add_argument("--results", type=Path, default=Path("docs/evaluation-review.json"))
    parser.add_argument(
        "--human-review", type=Path, default=Path("docs/evaluation-review.human-review.json")
    )
    parser.add_argument("--reproduction", type=Path, default=Path("docs/reproduction-review.json"))
    parser.add_argument("--output", type=Path, default=Path("docs/release-review.json"))
    parser.add_argument("--publish-dir", type=Path)
    parser.add_argument(
        "--learning-project",
        action="store_true",
        help="use explicitly user-authorized Human–AI assisted review for a learning-only release",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    settings = load_settings(root / "config/settings.yaml", project_root=root, environ={})
    registry = load_registry(settings.source_registry)
    active = ActiveRuntime(settings, registry, root)
    with active.open_request() as request:
        report = release_checks(
            args.cases.read_bytes(),
            args.results.read_bytes(),
            json.loads(args.human_review.read_bytes()),
            json.loads(args.reproduction.read_bytes()),
            request,
            implementation_id=implementation_hash(root),
            learning_project=args.learning_project,
        )
        report["publication_requested"] = args.publish_dir is not None
        _write_report(args.output, report)
        if args.publish_dir and report["release_approved"]:
            # Copy only explicitly selected config/evidence, never .env, usage DB or user data.
            release_dir = active.store.releases / request.pin.release.release_id
            files = {f"config/{p.name}": p for p in (root / "config").glob("*") if p.is_file()}
            files.update(
                {f"requirements/{p.name}": p for p in (root / "requirements").glob("*.lock")}
            )
            files.update(
                {
                    "evaluation/results.json": args.results,
                    "evaluation/human-review.json": args.human_review,
                    "reproduction.json": args.reproduction,
                    "runbook.md": root / "docs/operations-runbook.md",
                }
            )
            files.update({f"proof/{p.name}": p for p in release_dir.iterdir() if p.is_file()})
            publish_bundle(
                args.publish_dir,
                report,
                snapshot=request.pin.release.snapshot_directory,
                files=files,
            )
    print(json.dumps(report, indent=2))
    return 0 if report["release_approved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
