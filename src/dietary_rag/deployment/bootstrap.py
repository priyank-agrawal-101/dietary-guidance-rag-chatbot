"""Verify portable evidence; build, evaluate and activate an index on this host."""

import fcntl
import json
import logging
import os
import shutil
import tarfile
import tempfile
import threading
from hashlib import sha256
from pathlib import Path

MEMBERS = frozenset(
    {
        "evidence.sqlite",
        "manifest.json",
        "proof/freeze.json",
        "proof/regressions.json",
        "proof/heldout.json",
    }
)
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_EXTRACTED_BYTES = 40 * 1024 * 1024
LOGGER = logging.getLogger(__name__)


class SharedEmbedder:
    """One model per runtime; request readers still validate its configuration."""

    def __init__(self, factory):
        self.factory = factory
        self.value = None
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            if self.value is None:
                self.value = self.factory()
            return self.value


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def bootstrap_identity(root):
    from importlib.metadata import version

    root = Path(root)
    # UI copy, question buttons and answer rendering do not change index artifacts.
    config_files = (
        "deployment-artifacts.json",
        "sources.yaml",
        "document-aliases.json",
        "embedding-bge-small-en-v1.5.json",
        "tokenizer-e5-base-v2.json",
    )
    inputs = {name: digest(root / "config" / name) for name in config_files}
    from dietary_rag.settings import load_settings

    settings = load_settings(root / "config/settings.yaml", project_root=root, environ={})
    inputs["configuration"] = {
        name: getattr(settings, name).model_dump(mode="json")
        for name in ("embedding", "chunking", "runtime")
    }
    inputs["implementation"] = {
        p.relative_to(root).as_posix(): digest(p)
        for directory in ("corpus", "ingestion", "retrieval", "deployment")
        for p in sorted((root / "src/dietary_rag" / directory).rglob("*.py"))
    }
    inputs["implementation"]["providers/embeddings.py"] = digest(
        root / "src/dietary_rag/providers/embeddings.py"
    )
    inputs["dependencies"] = {
        n: version(n)
        for n in (
            "sentence-transformers",
            "transformers",
            "torch",
            "tokenizers",
            "numpy",
            "chromadb",
        )
    }
    return sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def model_cache_paths(root, cache_dir):
    """Pinned model files survive UI and corpus-version changes on retained disk."""
    root, cache_dir = Path(root), Path(cache_dir)
    return tuple(
        cache_dir / "models" / digest(root / "config" / name)
        for name in ("embedding-bge-small-en-v1.5.json", "tokenizer-e5-base-v2.json")
    )


def extract_verified(root, destination):
    """No extractall: accept exactly five regular, bounded, hash-checked files."""
    root, destination = Path(root), Path(destination)
    receipt = json.loads((root / "config/deployment-artifacts.json").read_text())
    if receipt["version"] != "streamlit_corpus_v1" or set(receipt["members"]) != MEMBERS:
        raise ValueError("unsupported portable corpus receipt")
    archive = root / "deployment/artifacts/corpus.tar.gz"
    if (
        archive.stat().st_size != receipt["bytes"]
        or receipt["bytes"] > MAX_ARCHIVE_BYTES
        or digest(archive) != receipt["sha256"]
    ):
        raise ValueError("portable corpus archive checksum or size mismatch")
    if any(
        type(v["bytes"]) is not int or not 0 <= v["bytes"] <= MAX_EXTRACTED_BYTES
        for v in receipt["members"].values()
    ):
        raise ValueError("invalid portable corpus member size")
    if sum(v["bytes"] for v in receipt["members"].values()) > MAX_EXTRACTED_BYTES:
        raise ValueError("portable corpus exceeds extraction limit")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, mode="r:gz") as stream:
        seen = set()
        for member in stream:
            if member.name not in MEMBERS or member.name in seen or not member.isfile():
                raise ValueError("unexpected, duplicate or unsafe corpus archive member")
            expected = receipt["members"][member.name]
            if member.size != expected["bytes"] or member.size < 0:
                raise ValueError("portable corpus member size mismatch")
            data = stream.extractfile(member).read(member.size + 1)
            if len(data) != member.size or sha256(data).hexdigest() != expected["sha256"]:
                raise ValueError("portable corpus member checksum mismatch")
            path = destination / member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as output:
                output.write(data)
            seen.add(member.name)
        if seen != MEMBERS:
            raise ValueError("portable corpus is incomplete")
    manifest = json.loads((destination / "manifest.json").read_text())
    if manifest["chunk_snapshot"]["snapshot_id"] != receipt["chunk_snapshot_id"]:
        raise ValueError("portable evidence identity mismatch")
    return receipt


def ensure_runtime(root, settings, registry, cache_dir, *, model_seed=None, tokenizer_seed=None):
    """Single-writer cold start. A corrupt published runtime fails closed."""
    from dietary_rag.api.runtime import ActiveRuntime
    from dietary_rag.corpus.index_snapshot import build_candidate
    from dietary_rag.ingestion.chunk_tokenizer import E5Tokenizer, prepare_tokenizer
    from dietary_rag.providers.embeddings import BGEEmbedder, prepare_embeddings
    from dietary_rag.retrieval.activation import validate_activation
    from dietary_rag.retrieval.contracts import RetrievalConfig
    from dietary_rag.retrieval.engine import Retriever
    from dietary_rag.retrieval.evaluation import (
        evaluate_retriever,
        load_frozen_cases,
        select_strategy,
    )
    from dietary_rag.retrieval.resolution import DocumentResolver

    root = Path(root).resolve()
    cache_dir = Path(cache_dir).resolve()
    work = cache_dir / bootstrap_identity(root)
    work.mkdir(parents=True, exist_ok=True)
    model, tokenizer_dir = model_cache_paths(root, cache_dir)
    model_manifest = root / "config/embedding-bge-small-en-v1.5.json"
    tokenizer_manifest = root / "config/tokenizer-e5-base-v2.json"
    shared_embedder = SharedEmbedder(lambda: BGEEmbedder(model, model_manifest, settings.embedding))
    active = ActiveRuntime(
        settings,
        registry,
        root,
        lifecycle_dir=work / "active",
        model_dir=model,
        tokenizer_dir=tokenizer_dir,
        query_embedder_factory=shared_embedder,
    )
    with (work / "bootstrap.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if active.store.pointer.exists():
            active.corpus_status()  # Includes full canonical/index/proof validation.
            return active
        # Model paths are shared by independent corpus builds; protect downloads too.
        with (cache_dir / "models.lock").open("a+b") as model_lock:
            fcntl.flock(model_lock.fileno(), fcntl.LOCK_EX)
            for seed, target, legacy_name in (
                (model_seed, model, "bge"),
                (tokenizer_seed, tokenizer_dir, "e5-tokenizer"),
            ):
                if seed is None and not target.exists():
                    # Reuse pinned files downloaded by the earlier cache layout.
                    seed = next(cache_dir.glob(f"*/models/{legacy_name}"), None)
                if seed is not None:
                    shutil.copytree(seed, target, dirs_exist_ok=True)
            LOGGER.info("Preparing pinned embedding model and tokenizer")
            prepare_embeddings(model_manifest, model)
            prepare_tokenizer(tokenizer_manifest, tokenizer_dir)
        source = Path(tempfile.mkdtemp(prefix=".evidence-", dir=work))
        try:
            receipt = extract_verified(root, source)
            LOGGER.info("Building native reviewed-source index")
            embedder = shared_embedder()
            tokenizer = E5Tokenizer(tokenizer_dir, tokenizer_manifest)
            directory = build_candidate(
                source / "evidence.sqlite",
                receipt["chunk_snapshot_id"],
                work / "snapshots",
                embedder,
                tokenizer,
                registry,
            )
            resolver = DocumentResolver(
                registry, json.loads((root / "config/document-aliases.json").read_text())
            )
            reports = []
            for strategy in ("dense", "bm25", "hybrid"):
                LOGGER.info("Evaluating frozen retrieval cases: %s", strategy)
                config = RetrievalConfig(
                    strategy=strategy,
                    resolution_configuration_id=resolver.configuration_id,
                    branch_candidates=settings.runtime.candidate_chunks,
                    candidate_chunks=settings.runtime.candidate_chunks,
                    per_document_candidates=settings.runtime.per_document_candidates,
                )
                with Retriever(
                    directory,
                    registry,
                    tokenizer,
                    embedder,
                    config,
                    resolver,
                    settings.runtime,
                    query_embedder_factory=lambda: embedder,
                ) as retriever:
                    cases, frozen = load_frozen_cases(
                        source / "proof/regressions.json",
                        source / "proof/heldout.json",
                        source / "proof/freeze.json",
                        retriever.manifest,
                        retriever.chunks,
                    )
                    reports.append(evaluate_retriever(retriever, cases, frozen))
            selection = select_strategy(reports)
            if selection["selected_strategy"] != "hybrid":
                raise ValueError("host evaluation did not select the approved hybrid strategy")
            config = RetrievalConfig.model_validate(selection["selected_configuration"])
            artifact = {
                "configuration": config.model_dump(mode="json"),
                "retrieval_configuration_id": config.configuration_id,
                "corpus_snapshot_id": selection["corpus_snapshot_id"],
            }
            active.store.activate(
                directory,
                artifact,
                selection,
                {
                    n: source / "proof" / n
                    for n in ("freeze.json", "regressions.json", "heldout.json")
                },
                lambda d, c, s, p: validate_activation(
                    d, c, s, p, registry, embedder, tokenizer, resolver
                ),
                expected_generation=0,
            )
            LOGGER.info("Validated hybrid corpus activated")
            (work / "bootstrap-report.json").write_text(
                json.dumps(
                    {
                        "version": "streamlit_bootstrap_v1",
                        "artifact_sha256": receipt["sha256"],
                        "selection": selection,
                        "host": os.uname().sysname,
                    },
                    indent=2,
                )
                + "\n"
            )
        finally:
            shutil.rmtree(source)
        active.corpus_status()
        return active
