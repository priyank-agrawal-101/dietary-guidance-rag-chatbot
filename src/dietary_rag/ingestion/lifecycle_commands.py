"""Validate and activate a complete pair, inspect its pointer, or roll it back."""

import json
from pathlib import Path


def register_commands(commands):
    activate = commands.add_parser(
        "activate-corpus", help="atomically publish a validated passing pair"
    )
    activate.add_argument("--snapshot-dir", type=Path)
    activate.add_argument(
        "--index-report", type=Path, default=Path("data/staging/index-build-report.json")
    )
    activate.add_argument(
        "--retrieval-config", type=Path, default=Path("config/retrieval-hybrid.json")
    )
    activate.add_argument("--selection-report", type=Path, required=True)
    for name, default in (
        ("regressions", "tests/fixtures/chunking/queries.json"),
        ("heldout", "tests/fixtures/retrieval/heldout.json"),
        ("freeze", "tests/fixtures/retrieval/freeze.json"),
    ):
        activate.add_argument(f"--{name}", type=Path, default=Path(default))
    rollback = commands.add_parser(
        "rollback-corpus", help="revalidate and restore the previous pair"
    )
    status = commands.add_parser(
        "corpus-status", help="inspect active and retained previous release"
    )
    for parser in (activate, rollback, status):
        parser.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
        parser.add_argument("--project-root", type=Path, default=Path.cwd())
        parser.add_argument("--lifecycle-dir", type=Path)
        parser.add_argument("--report", type=Path)
    for parser in (activate, rollback):
        parser.add_argument("--expected-generation", type=int)
        for name, default in (
            ("aliases", "config/document-aliases.json"),
            ("model-dir", "data/staging/models/bge-small-en-v1.5"),
            ("model-manifest", "config/embedding-bge-small-en-v1.5.json"),
            ("tokenizer-dir", "data/staging/tokenizers/e5-base-v2"),
            ("tokenizer-manifest", "config/tokenizer-e5-base-v2.json"),
        ):
            parser.add_argument(f"--{name}", type=Path, default=Path(default))


def run_command(args, settings, registry, write_report):
    from dietary_rag.corpus.lifecycle import LifecycleStore

    def path(value):
        return value if value.is_absolute() else args.project_root / value

    store = LifecycleStore(
        path(args.lifecycle_dir) if args.lifecycle_dir else settings.data_dir / "active"
    )
    if args.command != "corpus-status":
        from dietary_rag.ingestion.chunk_tokenizer import E5Tokenizer
        from dietary_rag.providers.embeddings import BGETextProcessor
        from dietary_rag.retrieval.activation import validate_activation
        from dietary_rag.retrieval.resolution import DocumentResolver

        if args.expected_generation is not None and args.expected_generation < 0:
            raise ValueError("expected generation must be zero or positive")
        processor = BGETextProcessor(
            path(args.model_dir), path(args.model_manifest), settings.embedding
        )
        tokenizer = E5Tokenizer(path(args.tokenizer_dir), path(args.tokenizer_manifest))
        if (
            settings.chunking.tokenizer_model_name != tokenizer.metadata["model_name"]
            or settings.chunking.tokenizer_revision != tokenizer.metadata["revision"]
        ):
            raise ValueError("configured tokenizer differs from historical evidence")
        resolver = DocumentResolver(registry, json.loads(path(args.aliases).read_text()))

        def validate(directory, config, proof, cases):
            return validate_activation(
                directory, config, proof, cases, registry, processor, tokenizer, resolver
            )

        if args.command == "activate-corpus":
            directory = (
                path(args.snapshot_dir)
                if args.snapshot_dir
                else path(Path(json.loads(path(args.index_report).read_text())["snapshot_dir"]))
            )
            state = store.activate(
                directory,
                json.loads(path(args.retrieval_config).read_bytes()),
                json.loads(path(args.selection_report).read_bytes()),
                {
                    "regressions.json": path(args.regressions),
                    "heldout.json": path(args.heldout),
                    "freeze.json": path(args.freeze),
                },
                validate,
                expected_generation=args.expected_generation,
            )
        else:
            state = store.rollback(validate, expected_generation=args.expected_generation)
    else:
        state = store.read_state()
    report = {
        "status": "no_active_corpus" if state is None else "active_pair_available",
        "active": state is not None,
        "state": state.model_dump(mode="json") if state else None,
        "lifecycle_directory": str(store.directory),
    }
    if state:
        report["active_release"] = store.read_release(state.active_release_id).model_dump(
            mode="json"
        )
        report["previous_release"] = None
        if state.previous_release_id:
            try:
                report["previous_release"] = store.read_release(
                    state.previous_release_id
                ).model_dump(mode="json")
            except (OSError, ValueError, KeyError) as exc:
                report["previous_release_error"] = str(exc)
    if args.report:
        write_report(path(args.report), report)
    print(json.dumps(report, indent=2))
    return 0
