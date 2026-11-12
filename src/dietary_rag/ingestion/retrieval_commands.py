"""Explicit candidate search and isolated-process strategy comparison; no activation."""

import json
import subprocess
import sys
import time
from pathlib import Path


def register_commands(commands):
    search = commands.add_parser("search-corpus", help="inspect scoped candidate evidence")
    search.add_argument("question")
    search.add_argument("--documents", nargs="+")
    search.add_argument("--document-name", action="append")
    search.add_argument("--source-tier", action="append")
    search.add_argument("--official-only", action="store_true")
    search.add_argument("--comparison", action="store_true")
    search.add_argument("--top-k", type=int, default=5)
    search.add_argument("--retrieval-config", type=Path)
    search.add_argument("--active", action="store_true")
    search.add_argument("--lifecycle-dir", type=Path)
    evaluate = commands.add_parser("evaluate-retrieval", help="compare frozen retrieval strategies")
    evaluate.add_argument("--regressions-only", action="store_true")
    evaluate.add_argument("--output-dir", type=Path, default=Path("data/staging/retrieval"))
    for name, default in (
        ("regressions", "tests/fixtures/chunking/queries.json"),
        ("heldout", "tests/fixtures/retrieval/heldout.json"),
        ("freeze", "tests/fixtures/retrieval/freeze.json"),
    ):
        evaluate.add_argument(f"--{name}", type=Path, default=Path(default))
    for parser in (search, evaluate):
        parser.add_argument("--strategy", choices=("dense", "bm25", "hybrid"))
        parser.add_argument("--snapshot-dir", type=Path)
        parser.add_argument("--report", type=Path)
        for name, default in (
            ("config", "config/settings.yaml"),
            ("index-report", "data/staging/index-build-report.json"),
            ("aliases", "config/document-aliases.json"),
            ("model-dir", "data/staging/models/bge-small-en-v1.5"),
            ("model-manifest", "config/embedding-bge-small-en-v1.5.json"),
            ("tokenizer-dir", "data/staging/tokenizers/e5-base-v2"),
            ("tokenizer-manifest", "config/tokenizer-e5-base-v2.json"),
        ):
            parser.add_argument(f"--{name}", type=Path, default=Path(default))
        parser.add_argument("--project-root", type=Path, default=Path.cwd())


def run_command(args, settings, registry, write_report):
    def path(value):
        return value if value.is_absolute() else args.project_root / value

    pin = None
    if args.command == "search-corpus":
        if args.active:
            if args.strategy or args.retrieval_config or args.snapshot_dir:
                raise ValueError(
                    "--active cannot override the pinned snapshot or retrieval configuration"
                )
            from dietary_rag.corpus.lifecycle import LifecycleStore

            store = LifecycleStore(
                path(args.lifecycle_dir) if args.lifecycle_dir else settings.data_dir / "active"
            )
            pin = store.pin()
        elif not args.strategy and not args.retrieval_config:
            raise ValueError("Choose --strategy, a compatible --retrieval-config, or --active")

    directory = (
        pin.release.snapshot_directory
        if pin
        else (
            path(args.snapshot_dir)
            if args.snapshot_dir
            else Path(json.loads(path(args.index_report).read_text())["snapshot_dir"])
        )
    )
    if not directory.is_absolute():
        directory = path(directory)
    if args.command == "evaluate-retrieval" and not args.strategy:
        from dietary_rag.retrieval.evaluation import select_strategy

        output = path(args.output_dir)
        output.mkdir(parents=True, exist_ok=True)
        reports = []
        for strategy in ("bm25", "dense", "hybrid"):
            destination = (
                output / f"{strategy}{'-regressions' if args.regressions_only else ''}.json"
            )
            command = [
                sys.executable,
                "-m",
                "dietary_rag",
                "evaluate-retrieval",
                "--strategy",
                strategy,
                "--snapshot-dir",
                str(directory),
                "--project-root",
                str(args.project_root),
                "--report",
                str(destination),
            ]
            for name in (
                "config",
                "aliases",
                "model_dir",
                "model_manifest",
                "tokenizer_dir",
                "tokenizer_manifest",
                "regressions",
                "heldout",
                "freeze",
            ):
                command.extend(["--" + name.replace("_", "-"), str(path(getattr(args, name)))])
            if args.regressions_only:
                command.append("--regressions-only")
            completed = subprocess.run(command, capture_output=True, text=True)
            if completed.returncode:
                raise RuntimeError(
                    f"{strategy} evaluation failed: {completed.stdout[-2000:]} {completed.stderr[-2000:]}"
                )
            reports.append(json.loads(destination.read_text()))
        report = (
            {"status": "regression_only_no_selection", "strategy_reports": reports}
            if args.regressions_only
            else select_strategy(reports)
        )
        destination = (
            path(args.report)
            if args.report
            else output / ("regressions.json" if args.regressions_only else "selection.json")
        )
        write_report(destination, report)
        if report.get("selected_strategy"):
            write_report(output / f"{report['selection_report_id']}.json", report)
            artifact = {
                "corpus_snapshot_id": report["corpus_snapshot_id"],
                "retrieval_configuration_id": report["selected_configuration_id"],
                "configuration": report["selected_configuration"],
                "active": False,
            }
            artifact_path = output / f"{artifact['retrieval_configuration_id']}.json"
            if artifact_path.exists() and json.loads(artifact_path.read_text()) != artifact:
                raise ValueError(
                    "existing versioned retrieval artifact differs; choose a new output directory"
                )
            write_report(artifact_path, artifact)
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "report": str(destination),
                    "selected_strategy": report.get("selected_strategy"),
                    "results": [
                        {
                            "strategy": r["strategy"],
                            "metrics": r["metrics"],
                            "performance": r["performance"],
                        }
                        for r in reports
                    ],
                },
                indent=2,
            )
        )
        return 1 if report["status"] == "no_passing_strategy_activation_blocked" else 0

    from dietary_rag.ingestion.chunk_tokenizer import E5Tokenizer
    from dietary_rag.providers.embeddings import BGETextProcessor
    from dietary_rag.retrieval.contracts import RetrievalConfig, SearchRequest
    from dietary_rag.retrieval.engine import Retriever
    from dietary_rag.retrieval.resolution import DocumentResolver

    preparation_started = time.perf_counter()
    resolver = DocumentResolver(registry, json.loads(path(args.aliases).read_text()))
    if pin:
        config = pin.release.configuration
    elif args.command == "search-corpus" and args.retrieval_config:
        artifact = json.loads(path(args.retrieval_config).read_text())
        config = RetrievalConfig.model_validate(artifact["configuration"])
        manifest = json.loads((directory / "manifest.json").read_text())
        if (
            artifact["retrieval_configuration_id"] != config.configuration_id
            or artifact["corpus_snapshot_id"] != manifest["snapshot_id"]
            or args.strategy
            and args.strategy != config.strategy
        ):
            raise ValueError("retrieval configuration/snapshot/strategy mismatch")
    else:
        if not args.strategy:
            raise ValueError("Choose --strategy or a compatible --retrieval-config")
        config = RetrievalConfig(
            strategy=args.strategy,
            resolution_configuration_id=resolver.configuration_id,
            branch_candidates=settings.runtime.candidate_chunks,
            candidate_chunks=settings.runtime.candidate_chunks,
            per_document_candidates=settings.runtime.per_document_candidates,
        )
    processor = BGETextProcessor(
        path(args.model_dir), path(args.model_manifest), settings.embedding
    )
    tokenizer = E5Tokenizer(path(args.tokenizer_dir), path(args.tokenizer_manifest))
    if (
        settings.chunking.tokenizer_model_name != tokenizer.metadata["model_name"]
        or settings.chunking.tokenizer_revision != tokenizer.metadata["revision"]
    ):
        raise ValueError("configured tokenizer differs from historical evidence")
    preparation_seconds = time.perf_counter() - preparation_started
    request_context = (
        pin.open_request(registry, tokenizer, processor, resolver, settings.runtime)
        if pin
        else Retriever(
            directory, registry, tokenizer, processor, config, resolver, settings.runtime
        )
    )
    with request_context as retriever:
        if args.command == "search-corpus":
            result = retriever.search(
                SearchRequest(
                    question=args.question,
                    selected_document_ids=tuple(args.documents) if args.documents else None,
                    document_names=tuple(args.document_name) if args.document_name else None,
                    source_tiers=tuple(args.source_tier) if args.source_tier else None,
                    official_only=args.official_only,
                    comparison=args.comparison,
                    limit=args.top_k,
                )
            )
            report = {
                "active": pin is not None,
                "retrieval_configuration": config.model_dump(mode="json"),
                **result.model_dump(mode="json"),
            }
        else:
            from dietary_rag.retrieval.evaluation import evaluate_retriever, load_frozen_cases

            cases, receipt = load_frozen_cases(
                path(args.regressions),
                path(args.heldout),
                path(args.freeze),
                retriever.manifest,
                retriever.chunks,
            )
            report = evaluate_retriever(
                retriever, cases, receipt, regressions_only=args.regressions_only
            )
            report["performance"]["configuration_preparation_seconds"] = preparation_seconds
            report["performance"]["total_startup_seconds"] = (
                preparation_seconds + retriever.startup_seconds
            )
    if args.report:
        write_report(path(args.report), report)
    print(json.dumps(report, indent=2))
    return 1 if args.command == "search-corpus" and report["audit"]["status"] != "searched" else 0
