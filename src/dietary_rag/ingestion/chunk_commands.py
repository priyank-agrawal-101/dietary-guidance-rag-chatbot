"""Explicit offline chunk builds and reproducible inspection reports."""

import json
from pathlib import Path

from dietary_rag.corpus.chunk_repository import ChunkRepository
from dietary_rag.corpus.identity import sha256_bytes
from dietary_rag.ingestion.chunk_tokenizer import E5Tokenizer, prepare_tokenizer
from dietary_rag.ingestion.chunking import StructureChunker
from dietary_rag.ingestion.normalization import parse_extraction
from dietary_rag.ingestion.normalization_contracts import NormalizedExtraction


def register_commands(commands):
    prepare = commands.add_parser(
        "prepare-tokenizer", help="explicitly cache pinned tokenizer files, no weights"
    )
    chunks = commands.add_parser(
        "chunk-sources", help="build reviewed, attributable staging chunks"
    )
    chunks.add_argument("--normalization-report", type=Path, required=True)
    chunks.add_argument(
        "--comparison", action="store_true", help="separate comparison-only snapshot"
    )
    chunks.add_argument("--target-tokens", type=int)
    chunks.add_argument("--overlap-tokens", type=int)
    chunks.add_argument("--output-dir", type=Path, default=Path("data/staging/chunks"))
    chunks.add_argument("--database", type=Path, default=Path("data/staging/chunks.sqlite"))
    chunks.add_argument("--report", type=Path, required=True)
    for parser in (prepare, chunks):
        parser.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
        parser.add_argument("--project-root", type=Path, default=Path.cwd())
        parser.add_argument(
            "--tokenizer-dir", type=Path, default=Path("data/staging/tokenizers/e5-base-v2")
        )
        parser.add_argument(
            "--tokenizer-manifest", type=Path, default=Path("config/tokenizer-e5-base-v2.json")
        )
    compare = commands.add_parser(
        "compare-chunks", help="offline BM25 diagnostic; no dense retrieval claims"
    )
    compare.add_argument("--baseline-report", type=Path, required=True)
    compare.add_argument("--candidate-report", type=Path, required=True)
    compare.add_argument("--cases", type=Path, required=True)
    compare.add_argument("--report", type=Path, required=True)
    compare.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
    compare.add_argument("--project-root", type=Path, default=Path.cwd())


def run_command(args, settings, registry, write_report):
    def path(value):
        return value if value.is_absolute() else args.project_root / value

    if args.command == "compare-chunks":
        from dietary_rag.ingestion.chunk_evaluation import compare_reports

        report = compare_reports(
            path(args.baseline_report),
            path(args.candidate_report),
            path(args.cases),
            args.project_root,
        )
        write_report(path(args.report), report)
        print(json.dumps(report, indent=2))
        return 0
    manifest_path = path(args.tokenizer_manifest)
    manifest = json.loads(manifest_path.read_text())
    if (
        settings.chunking.tokenizer_model_name != manifest["model_name"]
        or settings.chunking.tokenizer_revision != manifest["revision"]
    ):
        raise ValueError(
            "configured chunking tokenizer model/revision differs from pinned tokenizer"
        )
    if args.command == "prepare-tokenizer":
        prepare_tokenizer(manifest_path, path(args.tokenizer_dir))
        tokenizer = E5Tokenizer(path(args.tokenizer_dir), manifest_path)
        print(
            json.dumps(
                {"status": "pinned_tokenizer_ready", "tokenizer": tokenizer.metadata}, indent=2
            )
        )
        return 0
    tokenizer = E5Tokenizer(path(args.tokenizer_dir), manifest_path)
    chunker = StructureChunker(
        tokenizer,
        registry,
        target_tokens=args.target_tokens
        if args.target_tokens is not None
        else settings.chunking.target_tokens,
        overlap_tokens=args.overlap_tokens
        if args.overlap_tokens is not None
        else settings.chunking.overlap_tokens,
        role="comparison" if args.comparison else "consumer",
    )
    source_report = json.loads(path(args.normalization_report).read_text())
    rows = source_report.get("results", [])
    if not rows or len(rows) > 32:
        raise ValueError("chunk build requires 1–32 explicit normalized artifacts")
    entries, results, skipped = [], [], []
    for row in rows:
        # Only the reviewed artifact determines eligibility; report flags are informational.
        try:
            if "input" not in row or "output" not in row:
                raise ValueError("normalization report lacks original/normalized artifact paths")
            norm_path, native_path = path(Path(row["output"])), path(Path(row["input"]))
            if max(norm_path.stat().st_size, native_path.stat().st_size) > 20_000_000:
                raise ValueError("chunk input artifact exceeds 20 MB")
            norm = NormalizedExtraction.model_validate_json(norm_path.read_bytes())
            if norm.comparison_only != args.comparison:
                skipped.append(
                    {
                        "document_id": norm.document.document_id,
                        "reason": "comparison/default role not selected",
                    }
                )
                continue
            data = native_path.read_bytes()
            if sha256_bytes(data) != norm.original_artifact_sha256:
                raise ValueError(
                    "original native artifact bytes differ from reviewed normalization receipt"
                )
            native = parse_extraction(json.loads(data))
            build = chunker.build(norm, native)
            output = path(args.output_dir) / f"{build.build_id}.json"
            write_report(output, build.model_dump(mode="json"))
            results.append(
                {
                    "document_id": norm.document.document_id,
                    "status": build.state,
                    "build_id": build.build_id,
                    "normalization_id": norm.normalization_id,
                    "chunks": len(build.chunks),
                    "output": str(output),
                    "diagnostics": list(build.diagnostics),
                }
            )
            entries.append((build, norm, native))
        except (OSError, ValueError, KeyError) as exc:
            results.append(
                {"document_id": row.get("document_id"), "status": "failed", "error": str(exc)}
            )
    passed = bool(entries) and all(r["status"] == "ready" for r in results)
    snapshot = None
    if passed:
        with ChunkRepository(path(args.database)) as repository:
            snapshot = repository.save_snapshot(entries, tokenizer)
    report = {
        "status": "reviewed_staging_chunks" if passed else "chunk_build_rejected",
        "active": False,
        "role": chunker.role,
        "target_tokens": chunker.target,
        "overlap_tokens": chunker.overlap,
        "input_limit": tokenizer.limit,
        "tokenizer": tokenizer.metadata,
        "database": str(path(args.database)),
        "snapshot": snapshot,
        "results": results,
        "skipped": skipped,
    }
    write_report(path(args.report), report)
    print(json.dumps(report, indent=2))
    return 0 if passed else 1
