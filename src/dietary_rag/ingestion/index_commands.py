"""Explicit model preparation and candidate indexing; no activation or search."""

import json
from pathlib import Path


def register_commands(commands):
    prepare = commands.add_parser(
        "prepare-embeddings", help="download and verify pinned BGE-small artifacts"
    )
    build = commands.add_parser(
        "build-index", help="build a paired candidate evidence/vector snapshot"
    )
    build.add_argument("--chunk-snapshot", required=True)
    build.add_argument("--chunk-database", type=Path, default=Path("data/staging/chunks.sqlite"))
    build.add_argument("--output-dir", type=Path, default=Path("data/staging/indexes"))
    check = commands.add_parser(
        "validate-index", help="verify candidate model/evidence/vector compatibility"
    )
    check.add_argument("--snapshot-dir", type=Path, required=True)
    for parser in (prepare, build, check):
        parser.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
        parser.add_argument("--project-root", type=Path, default=Path.cwd())
        parser.add_argument(
            "--model-dir", type=Path, default=Path("data/staging/models/bge-small-en-v1.5")
        )
        parser.add_argument(
            "--model-manifest", type=Path, default=Path("config/embedding-bge-small-en-v1.5.json")
        )
        parser.add_argument("--report", type=Path)
    for parser in (build, check):
        parser.add_argument(
            "--tokenizer-dir", type=Path, default=Path("data/staging/tokenizers/e5-base-v2")
        )
        parser.add_argument(
            "--tokenizer-manifest", type=Path, default=Path("config/tokenizer-e5-base-v2.json")
        )


def run_command(args, settings, registry, write_report):
    from dietary_rag.providers.embeddings import BGEEmbedder, load_manifest, prepare_embeddings

    def path(value):
        return value if value.is_absolute() else args.project_root / value

    manifest = load_manifest(path(args.model_manifest))
    if (
        settings.embedding.model_name != manifest["model_name"]
        or settings.embedding.revision != manifest["revision"]
    ):
        raise ValueError("configured embedding model/revision differs from pinned model")
    if args.command == "prepare-embeddings":
        prepare_embeddings(path(args.model_manifest), path(args.model_dir))
        report = {"status": "pinned_embedding_artifacts_ready", "model": manifest}
    else:
        from dietary_rag.corpus.index_snapshot import build_candidate, validate_candidate
        from dietary_rag.ingestion.chunk_tokenizer import E5Tokenizer

        tokenizer = E5Tokenizer(path(args.tokenizer_dir), path(args.tokenizer_manifest))
        if (
            settings.chunking.tokenizer_model_name != tokenizer.metadata["model_name"]
            or settings.chunking.tokenizer_revision != tokenizer.metadata["revision"]
        ):
            raise ValueError("configured chunking tokenizer differs from historical evidence")
        embedder = BGEEmbedder(path(args.model_dir), path(args.model_manifest), settings.embedding)
        if args.command == "build-index":
            destination = build_candidate(
                path(args.chunk_database),
                args.chunk_snapshot,
                path(args.output_dir),
                embedder,
                tokenizer,
                registry,
            )
            report = json.loads((destination / "validation.json").read_text())
            report["snapshot_dir"] = str(destination)
        else:
            report = validate_candidate(path(args.snapshot_dir), embedder, tokenizer, registry)
    if args.report:
        write_report(path(args.report), report)
    print(json.dumps(report, indent=2))
    return 0
