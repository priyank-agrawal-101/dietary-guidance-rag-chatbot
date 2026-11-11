"""Content-addressed original bytes; explicit writes never replace an existing artifact."""

import os
import re
import tempfile
from pathlib import Path

from dietary_rag.corpus.contracts import Contract, PositiveInt, Sha256
from dietary_rag.corpus.identity import sha256_bytes


class ArtifactIntegrityError(ValueError):
    """Stored bytes/path no longer match their recorded identity."""


class RawArtifact(Contract):
    sha256: Sha256
    byte_count: PositiveInt
    relative_path: Path


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, digest: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("artifact identity must be a lowercase SHA-256 digest")
        path = self.root / "sha256" / digest[:2] / f"{digest}.blob"
        if not path.parent.resolve().is_relative_to(self.root) or path.is_symlink():
            raise ArtifactIntegrityError("artifact path leaves its store or is a symlink")
        return path

    def put_bytes(self, content: bytes) -> RawArtifact:
        if not isinstance(content, bytes) or not content:
            raise ValueError("raw artifacts require nonempty original bytes")
        digest = sha256_bytes(content)
        destination = self._path(digest)
        if destination.exists():
            return self.describe(digest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # A same-directory temporary file is flushed before exclusive publication.
        fd, temporary_name = tempfile.mkstemp(prefix=".artifact-", dir=destination.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(
                    temporary, destination
                )  # Atomic create; never overwrite a competing writer.
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
        return self.describe(digest)

    def read_bytes(self, digest: str) -> bytes:
        content = self._path(digest).read_bytes()
        if sha256_bytes(content) != digest:
            raise ArtifactIntegrityError("stored artifact bytes do not match their SHA-256")
        return content

    def describe(self, digest: str) -> RawArtifact:
        content = self.read_bytes(digest)
        return RawArtifact(
            sha256=digest,
            byte_count=len(content),
            relative_path=self._path(digest).relative_to(self.root),
        )
