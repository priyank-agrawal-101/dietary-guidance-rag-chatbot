"""Shared lazy active-corpus runtime for CLI, HTTP chat and readiness inspection."""

import json
import os
import threading
import time
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

from dietary_rag.answering.adapters import ExtractiveGenerator
from dietary_rag.answering.service import AnswerService
from dietary_rag.api.context import remaining_seconds
from dietary_rag.policy.runtime import PolicyRuntime
from dietary_rag.policy.service import PolicyService
from dietary_rag.providers.groq import GroqGenerator
from dietary_rag.providers.usage import UsageLedger


def load_environment(project_root, env_file=None):
    """Read .env explicitly at entry points; exported values take precedence."""
    values = {}
    path = Path(env_file) if env_file is not None else Path(project_root) / ".env"
    if not path.is_absolute():
        path = Path(project_root) / path
    if path.is_file():
        from dotenv import dotenv_values

        values.update(
            {
                key: value
                for key, value in dotenv_values(path, interpolate=False).items()
                if value is not None
            }
        )
    values.update(os.environ)
    return values


class ActiveRuntime:
    def __init__(
        self,
        settings,
        registry,
        project_root,
        *,
        lifecycle_dir=None,
        aliases=None,
        model_dir=None,
        model_manifest=None,
        tokenizer_dir=None,
        tokenizer_manifest=None,
    ):
        self.settings, self.registry = settings, registry
        # Chroma's embedded clients share a cached system which close_chroma stops.
        # Keep the complete pinned read context exclusive, including citation checks.
        self._corpus_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._status_cache = None
        root = Path(project_root)

        def path(value, default):
            value = Path(value) if value is not None else Path(default)
            return value if value.is_absolute() else root / value

        from dietary_rag.corpus.lifecycle import LifecycleStore

        self.store = LifecycleStore(path(lifecycle_dir, settings.data_dir / "active"))
        self.aliases = path(aliases, "config/document-aliases.json")
        self.model_dir = path(model_dir, "data/staging/models/bge-small-en-v1.5")
        self.model_manifest = path(model_manifest, "config/embedding-bge-small-en-v1.5.json")
        self.tokenizer_dir = path(tokenizer_dir, "data/staging/tokenizers/e5-base-v2")
        self.tokenizer_manifest = path(tokenizer_manifest, "config/tokenizer-e5-base-v2.json")

    @contextmanager
    def open_request(self):
        if not self._corpus_lock.acquire(
            timeout=remaining_seconds(self.settings.runtime.request_timeout_seconds)
        ):
            raise TimeoutError("corpus access deadline exceeded")
        try:
            with self._open_request() as request:
                yield request
        finally:
            self._corpus_lock.release()

    @contextmanager
    def _open_request(self):
        from dietary_rag.ingestion.chunk_tokenizer import E5Tokenizer
        from dietary_rag.providers.embeddings import BGETextProcessor
        from dietary_rag.retrieval.resolution import DocumentResolver

        pin = self.store.pin()
        resolver = DocumentResolver(self.registry, json.loads(self.aliases.read_text()))
        processor = BGETextProcessor(self.model_dir, self.model_manifest, self.settings.embedding)
        tokenizer = E5Tokenizer(self.tokenizer_dir, self.tokenizer_manifest)
        if (
            self.settings.chunking.tokenizer_model_name != tokenizer.metadata["model_name"]
            or self.settings.chunking.tokenizer_revision != tokenizer.metadata["revision"]
        ):
            raise ValueError("configured tokenizer differs from historical evidence")
        with pin.open_request(
            self.registry, tokenizer, processor, resolver, self.settings.runtime
        ) as request:
            yield request

    def corpus_status(self):
        # Coalesce status checks; cache only metadata from full validation. Chat
        # still opens and validates its own pin on every request.
        if not self._status_lock.acquire(
            timeout=remaining_seconds(self.settings.runtime.request_timeout_seconds)
        ):
            raise TimeoutError("source status deadline exceeded")
        try:
            pin = self.store.pin()
            manifest = pin.release.snapshot_directory / "manifest.json"
            if sha256(manifest.read_bytes()).hexdigest() != pin.release.manifest_sha256:
                raise ValueError("pinned snapshot manifest changed")
            key = (pin.generation, pin.release.release_id)
            if self._status_cache is not None:
                cached_key, expires, state = self._status_cache
                if cached_key == key and time.monotonic() < expires:
                    return {**state, "documents": dict(state["documents"])}
            with self.open_request() as request:
                state = {
                    "corpus_version": request.pin.release.corpus_snapshot_id,
                    "release_id": request.pin.release.release_id,
                    "activation_generation": request.pin.generation,
                    "documents": dict(request.retriever.documents),
                }
                key = (request.pin.generation, request.pin.release.release_id)
            self._status_cache = (key, time.monotonic() + 30, state)
            return {**state, "documents": dict(state["documents"])}
        finally:
            self._status_lock.release()


def build_answer_service(settings, active_runtime, *, environ=None, offline=False, transport=None):
    runtime = PolicyRuntime(
        active_runtime.open_request,
        runtime=settings.runtime,
        policy=PolicyService(settings.policy),
    )
    ledger = UsageLedger(settings.data_dir / "usage" / "groq.sqlite", settings.groq)
    generator = None
    if offline:
        generator = ExtractiveGenerator()
    elif settings.generation.provider == "groq":
        generator = GroqGenerator(
            settings.generation.model_name,
            settings.groq,
            ledger,
            api_key=(environ or {}).get("GROQ_API_KEY"),
            timeout_seconds=settings.answering.generator_timeout_seconds,
            transport=transport,
        )
    return AnswerService(runtime, settings=settings.answering, generator=generator), ledger
