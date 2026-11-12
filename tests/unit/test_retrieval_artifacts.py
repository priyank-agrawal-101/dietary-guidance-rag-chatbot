"""Checked-in selected configuration and frozen evidence agree with the review."""

import json
from pathlib import Path

from dietary_rag.corpus.identity import sha256_bytes
from dietary_rag.retrieval.contracts import RetrievalConfig
from dietary_rag.retrieval.resolution import DocumentResolver

ROOT = Path(__file__).resolve().parents[2]


def test_checked_in_configuration_matches_measured_review_and_registry(reviewed_registry):
    artifact = json.loads((ROOT / "config/retrieval-hybrid.json").read_text())
    review = json.loads((ROOT / "docs/retrieval-review.json").read_text())
    aliases = json.loads((ROOT / "config/document-aliases.json").read_text())
    config = RetrievalConfig.model_validate(artifact["configuration"])
    resolver = DocumentResolver(reviewed_registry, aliases)
    assert (
        config.configuration_id
        == artifact["retrieval_configuration_id"]
        == review["selected_configuration_id"]
    )
    assert config.resolution_configuration_id == resolver.configuration_id
    assert config.model_dump() == review["selected_configuration"]
    assert artifact["corpus_snapshot_id"] == review["corpus_snapshot_id"]
    assert config.strategy == "hybrid"
    assert not artifact["active"] and not review["active"]


def test_frozen_cases_and_review_receipt_unchanged():
    freeze = json.loads((ROOT / "tests/fixtures/retrieval/freeze.json").read_text())
    review = json.loads((ROOT / "docs/retrieval-review.json").read_text())
    assert freeze == review["case_freeze"]
    assert freeze["heldout_sha256"] == sha256_bytes(
        (ROOT / "tests/fixtures/retrieval/heldout.json").read_bytes()
    )
    assert freeze["regressions_sha256"] == sha256_bytes(
        (ROOT / "tests/fixtures/chunking/queries.json").read_bytes()
    )
