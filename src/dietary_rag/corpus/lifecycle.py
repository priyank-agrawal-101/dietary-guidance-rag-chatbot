"""Atomic local publication, immutable release records and request-scoped evidence.

Snapshots are retained in place and never modified or garbage-collected here.
Unix advisory locks serialize publishers; readers use one atomic pointer read.
"""

import fcntl
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import model_validator

from dietary_rag.corpus.contracts import Contract, Identifier, PositiveInt
from dietary_rag.corpus.identity import sha256_bytes, stable_id
from dietary_rag.retrieval.contracts import RetrievalConfig

CASE_FILES = ("regressions.json", "heldout.json", "freeze.json")


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_file(path, content):
    with Path(path).open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


class Release(Contract):
    version: Literal["corpus_release_v1"] = "corpus_release_v1"
    snapshot_directory: Path
    corpus_snapshot_id: Identifier
    embedding_configuration_id: Identifier
    retrieval_configuration_id: Identifier
    configuration: RetrievalConfig
    selection_report_id: Identifier
    selection_sha256: str
    manifest_sha256: str
    case_file_hashes: dict[str, str]
    validation: dict

    @property
    def release_id(self):
        return stable_id("corpus_release", self.model_dump(mode="json"))

    @model_validator(mode="after")
    def compatibility(self):
        if not self.snapshot_directory.is_absolute():
            raise ValueError("release snapshot directory must be absolute")
        if self.configuration.configuration_id != self.retrieval_configuration_id:
            raise ValueError("release retrieval configuration identity mismatch")
        if set(self.case_file_hashes) != set(CASE_FILES):
            raise ValueError("release must retain all three frozen case files")
        if (
            self.validation.get("snapshot_id") != self.corpus_snapshot_id
            or self.validation.get("embedding_configuration_id") != self.embedding_configuration_id
        ):
            raise ValueError("release validation differs from pinned snapshot/configuration")
        return self


class ActiveState(Contract):
    version: Literal["active_corpus_v1"] = "active_corpus_v1"
    generation: PositiveInt
    active_release_id: Identifier
    previous_release_id: Identifier | None = None


class LifecycleStore:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.releases = self.directory / "releases"
        self.pointer = self.directory / "active.json"

    @contextmanager
    def _lock(self):
        self.releases.mkdir(parents=True, exist_ok=True)
        with (self.directory / "publication.lock").open("a+b") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def read_state(self):
        try:
            raw = json.loads(self.pointer.read_bytes())
        except FileNotFoundError:
            return None
        state = ActiveState.model_validate(raw["state"])
        if raw.get("state_id") != stable_id("active_corpus", state.model_dump()):
            raise ValueError("active pointer identity mismatch; no fallback allowed")
        return state

    def _write_state(self, state):
        content = json_bytes(
            {
                "state_id": stable_id("active_corpus", state.model_dump()),
                "state": state.model_dump(),
            }
        )
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.pointer)
            sync_directory(self.directory)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def read_release(self, identifier):
        # Identifiers may come from the pointer; disallow path traversal even
        # when a malformed local pointer was given a recomputed hash.
        if not identifier.startswith("corpus_release_") or len(identifier) != 79:
            raise ValueError("invalid release identifier")
        if any(c not in "0123456789abcdef" for c in identifier[15:]):
            raise ValueError("invalid release identifier")
        directory = self.releases / identifier
        raw = json.loads((directory / "release.json").read_bytes())
        release = Release.model_validate(raw["release"])
        if release.release_id != identifier or raw.get("release_id") != identifier:
            raise ValueError("immutable release identity mismatch")
        for name, digest in {
            "selection.json": release.selection_sha256,
            **release.case_file_hashes,
        }.items():
            if sha256_bytes((directory / name).read_bytes()) != digest:
                raise ValueError(f"retained release artifact changed: {name}")
        if (
            sha256_bytes((release.snapshot_directory / "manifest.json").read_bytes())
            != release.manifest_sha256
        ):
            raise ValueError("pinned snapshot manifest changed")
        return release

    @staticmethod
    def _expect_generation(state, expected):
        generation = state.generation if state else 0
        if expected is not None and expected != generation:
            raise ValueError(
                f"publication generation changed: expected {expected}, current {generation}"
            )

    def activate(
        self,
        snapshot_directory,
        artifact,
        selection,
        case_files,
        validate,
        *,
        expected_generation=None,
    ):
        snapshot_directory = Path(snapshot_directory).resolve()
        config = RetrievalConfig.model_validate(artifact["configuration"])
        if artifact["retrieval_configuration_id"] != config.configuration_id:
            raise ValueError("candidate retrieval configuration identity mismatch")
        if set(case_files) != set(CASE_FILES):
            raise ValueError("activation requires frozen regression, held-out and receipt files")
        with self._lock():
            previous = self.read_state()
            self._expect_generation(previous, expected_generation)
            if previous:
                self.read_release(previous.active_release_id)
            temporary = Path(tempfile.mkdtemp(prefix=".release-", dir=self.releases))
            try:
                proof_bytes = json_bytes(selection)
                write_file(temporary / "selection.json", proof_bytes)
                for name in CASE_FILES:
                    write_file(temporary / name, Path(case_files[name]).read_bytes())
                validation = validate(snapshot_directory, config, selection, temporary)
                if artifact["corpus_snapshot_id"] != validation["snapshot_id"]:
                    raise ValueError("candidate configuration is paired with a different corpus")
                release = Release(
                    snapshot_directory=snapshot_directory,
                    corpus_snapshot_id=validation["snapshot_id"],
                    embedding_configuration_id=validation["embedding_configuration_id"],
                    retrieval_configuration_id=config.configuration_id,
                    configuration=config,
                    selection_report_id=selection["selection_report_id"],
                    selection_sha256=sha256_bytes(proof_bytes),
                    manifest_sha256=sha256_bytes(
                        (snapshot_directory / "manifest.json").read_bytes()
                    ),
                    case_file_hashes={
                        name: sha256_bytes((temporary / name).read_bytes()) for name in CASE_FILES
                    },
                    validation=validation,
                )
                write_file(
                    temporary / "release.json",
                    json_bytes(
                        {
                            "release_id": release.release_id,
                            "release": release.model_dump(mode="json"),
                        }
                    ),
                )
                sync_directory(temporary)
                destination = self.releases / release.release_id
                if destination.exists():
                    if self.read_release(release.release_id) != release:
                        raise ValueError("existing immutable release differs")
                else:
                    os.rename(temporary, destination)
                    sync_directory(self.releases)
                if previous and previous.active_release_id == release.release_id:
                    return previous  # Idempotence preserves the rollback target.
                state = ActiveState(
                    generation=previous.generation + 1 if previous else 1,
                    active_release_id=release.release_id,
                    previous_release_id=previous.active_release_id if previous else None,
                )
                self._write_state(state)
                return state
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)

    def rollback(self, validate, *, expected_generation=None):
        with self._lock():
            current = self.read_state()
            self._expect_generation(current, expected_generation)
            if current is None or current.previous_release_id is None:
                raise ValueError("no previous active release is available for rollback")
            release = self.read_release(current.previous_release_id)
            directory = self.releases / release.release_id
            proof = json.loads((directory / "selection.json").read_bytes())
            validation = validate(
                release.snapshot_directory, release.configuration, proof, directory
            )
            if validation != release.validation:
                raise ValueError("rollback compatibility validation differs from retained release")
            state = ActiveState(
                generation=current.generation + 1,
                active_release_id=release.release_id,
                previous_release_id=current.active_release_id,
            )
            self._write_state(state)
            return state

    def pin(self):
        state = self.read_state()  # Exactly one pointer read for this request.
        if state is None:
            raise ValueError("no active corpus; explicit candidate search remains available")
        return PinnedRelease(state.generation, self.read_release(state.active_release_id))


@dataclass(frozen=True)
class PinnedRelease:
    generation: int
    release: Release

    @contextmanager
    def open_request(self, registry, tokenizer, processor, resolver, runtime, **kwargs):
        from dietary_rag.retrieval.engine import Retriever

        if processor.configuration_id != self.release.embedding_configuration_id:
            raise ValueError("active request embedding configuration mismatch")
        if (
            sha256_bytes((self.release.snapshot_directory / "manifest.json").read_bytes())
            != self.release.manifest_sha256
        ):
            raise ValueError("pinned snapshot manifest changed")
        with Retriever(
            self.release.snapshot_directory,
            registry,
            tokenizer,
            processor,
            self.release.configuration,
            resolver,
            runtime,
            **kwargs,
        ) as retriever:
            if retriever.manifest["snapshot_id"] != self.release.corpus_snapshot_id:
                raise ValueError("active request corpus identity mismatch")
            yield PinnedRequest(self, retriever)


class PinnedRequest:
    def __init__(self, pin, retriever):
        self.pin, self.retriever = pin, retriever
        self.returned_chunks = set()

    def search(self, request):
        result = self.retriever.search(request)
        if (
            result.audit.corpus_snapshot_id != self.pin.release.corpus_snapshot_id
            or result.audit.retrieval_configuration_id
            != self.pin.release.retrieval_configuration_id
        ):
            raise ValueError("request evidence differs from pinned release")
        self.returned_chunks.update(hit.chunk.chunk_id for hit in result.hits)
        return result.model_copy(
            update={
                "audit": result.audit.model_copy(
                    update={
                        "release_id": self.pin.release.release_id,
                        "activation_generation": self.pin.generation,
                    }
                )
            }
        )

    def resolve_evidence(self, chunk_id):
        if chunk_id not in self.returned_chunks:
            raise ValueError("citation evidence was not retrieved for this pinned request")
        resolved = self.retriever.evidence.resolve_chunk(chunk_id)
        if resolved["chunk"] != self.retriever.chunks[chunk_id]:
            raise ValueError("pinned citation evidence changed")
        return resolved
