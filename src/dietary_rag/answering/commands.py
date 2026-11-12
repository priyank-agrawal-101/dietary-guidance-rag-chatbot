"""Cited active-corpus CLI; policy executes before model/tokenizer/corpus loading."""

import json
from contextlib import contextmanager
from pathlib import Path

from dietary_rag.answering.citations import render_text
from dietary_rag.answering.service import AnswerService
from dietary_rag.policy.runtime import PolicyRuntime
from dietary_rag.policy.service import PolicyService


def register_commands(commands):
    parser = commands.add_parser(
        "answer-question", help="answer from validated pinned active evidence"
    )
    parser.add_argument("question")
    parser.add_argument("--documents", nargs="+")
    parser.add_argument("--official-only", action="store_true")
    parser.add_argument("--source-tier", action="append")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--lifecycle-dir", type=Path)
    parser.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    for name, default in (
        ("aliases", "config/document-aliases.json"),
        ("model-dir", "data/staging/models/bge-small-en-v1.5"),
        ("model-manifest", "config/embedding-bge-small-en-v1.5.json"),
        ("tokenizer-dir", "data/staging/tokenizers/e5-base-v2"),
        ("tokenizer-manifest", "config/tokenizer-e5-base-v2.json"),
    ):
        parser.add_argument(f"--{name}", type=Path, default=Path(default))


def run_command(args, settings, registry, write_report):
    def path(value):
        return value if value.is_absolute() else args.project_root / value

    @contextmanager
    def opener():
        from dietary_rag.corpus.lifecycle import LifecycleStore
        from dietary_rag.ingestion.chunk_tokenizer import E5Tokenizer
        from dietary_rag.providers.embeddings import BGETextProcessor
        from dietary_rag.retrieval.resolution import DocumentResolver

        pin = LifecycleStore(
            path(args.lifecycle_dir) if args.lifecycle_dir else settings.data_dir / "active"
        ).pin()
        resolver = DocumentResolver(registry, json.loads(path(args.aliases).read_text()))
        processor = BGETextProcessor(
            path(args.model_dir), path(args.model_manifest), settings.embedding
        )
        tokenizer = E5Tokenizer(path(args.tokenizer_dir), path(args.tokenizer_manifest))
        if (
            settings.chunking.tokenizer_model_name != tokenizer.metadata["model_name"]
            or settings.chunking.tokenizer_revision != tokenizer.metadata["revision"]
        ):
            raise ValueError("configured tokenizer differs from historical evidence")
        with pin.open_request(
            registry, tokenizer, processor, resolver, settings.runtime
        ) as request:
            yield request

    runtime = PolicyRuntime(opener, runtime=settings.runtime, policy=PolicyService(settings.policy))
    payload = {"question": args.question, "official_only": args.official_only, "limit": args.top_k}
    if args.documents is not None:
        payload["scope"] = {"mode": "selected", "document_ids": args.documents}
    if args.source_tier:
        payload["source_tiers"] = args.source_tier
    inspection = AnswerService(runtime, settings=settings.answering).inspect(payload)
    if args.report:
        write_report(path(args.report), inspection.model_dump())
    print(
        render_text(inspection.response)
        if args.format == "text"
        else json.dumps(inspection.response.model_dump(mode="json"), indent=2)
    )
    return 1 if hasattr(inspection.response, "code") else 0
