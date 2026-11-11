"""Small shared interface; implementations are introduced during extraction tasks."""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from dietary_rag.corpus.contracts import ContentBlock, DocumentVersion


class Extractor(Protocol):
    def extract(self, raw_path: Path, document: DocumentVersion) -> Sequence[ContentBlock]:
        """Return ordered, location-preserving blocks; never activate a corpus."""
        ...
