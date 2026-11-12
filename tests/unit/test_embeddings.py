"""Embedding input fidelity, hard limits and pinned artifact rejection."""

import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from dietary_rag.providers.embeddings import (
    QUERY_PREFIX,
    BGEEmbedder,
    load_manifest,
    passage_input,
    prepare_embeddings,
    validate_vectors,
    verify_artifacts,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "config/embedding-bge-small-en-v1.5.json"


def test_passage_removes_only_model_prefix_and_preserves_context_and_body():
    original = "passage: Document: Source\nAudience: consumers\n\nDo not freeze. passage: remains."
    assert passage_input(SimpleNamespace(embedding_text=original)) == original[9:]
    with pytest.raises(ValueError, match="prefix"):
        passage_input(SimpleNamespace(embedding_text="wrong representation"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("model_name", "other"),
        ("revision", "main"),
        ("dimensions", 768),
        ("pooling", "mean"),
        ("metric", "l2"),
        ("max_tokens", 8192),
    ],
)
def test_manifest_rejects_model_configuration_changes(tmp_path, field, value):
    manifest = load_manifest(MANIFEST)
    manifest[field] = value
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="unsupported"):
        load_manifest(path)


def test_manifest_rejects_unpinned_artifacts_and_missing_cache(tmp_path):
    manifest = load_manifest(MANIFEST)
    with pytest.raises(ValueError, match="missing/incomplete"):
        verify_artifacts(tmp_path, manifest)
    manifest["files"].pop("model.safetensors")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="artifact set"):
        load_manifest(path)


def test_corrupted_cache_is_rejected_before_model_loading(tmp_path):
    manifest = load_manifest(MANIFEST)
    (tmp_path / "config.json").write_bytes(b"x" * manifest["files"]["config.json"]["size"])
    with pytest.raises(ValueError, match="hash differs"):
        verify_artifacts(tmp_path, manifest)


@pytest.mark.parametrize("content", [b"bad", b"too many bytes"])
def test_failed_download_cleans_partial_files_and_preserves_existing_cache(
    tmp_path, monkeypatch, content
):
    item = {"sha256": "0" * 64, "size": 3}
    monkeypatch.setattr(
        "dietary_rag.providers.embeddings.load_manifest", lambda p: {"files": {"config.json": item}}
    )
    response = BytesIO(content)
    response.url = "https://huggingface.co/test"
    monkeypatch.setattr("dietary_rag.providers.embeddings.urlopen", lambda *a, **k: response)
    old = tmp_path / "config.json"
    old.write_bytes(b"old")
    with pytest.raises(ValueError, match="download"):
        prepare_embeddings(MANIFEST, tmp_path)
    assert old.read_bytes() == b"old" and list(tmp_path.iterdir()) == [old]


@pytest.mark.parametrize(
    "vectors,count", [([], 1), ([[0.0] * 384], 1), ([[1.0] * 768], 1), ([[float("nan")] * 384], 1)]
)
def test_vector_validation_rejects_invalid_inference(vectors, count):
    with pytest.raises(ValueError):
        validate_vectors(vectors, count)


def test_query_limit_rejects_before_inference_and_prefix_is_applied_once():
    adapter = BGEEmbedder.__new__(BGEEmbedder)
    seen = []
    adapter.encode = lambda texts: seen.extend(texts) or [[1.0] + [0.0] * 383]
    adapter.embed_query("How should I chill food?")
    assert seen == [QUERY_PREFIX + "How should I chill food?"]
    with pytest.raises(ValueError, match="instruction"):
        adapter.embed_query(QUERY_PREFIX + "Question")
    with pytest.raises(ValueError, match="nonempty"):
        adapter.embed_query(" ")
    del adapter.encode
    adapter.count = lambda text: 513
    adapter.model = None  # A rejected input must never reach model inference.
    with pytest.raises(ValueError, match="truncation forbidden"):
        adapter.encode(["long question"])


def test_passage_overlimit_requires_reviewed_rebuild():
    adapter = BGEEmbedder.__new__(BGEEmbedder)
    adapter.count = lambda text: 513
    with pytest.raises(ValueError, match="reviewed rebuild"):
        adapter.input_record(SimpleNamespace(chunk_id="chunk_1", embedding_text="passage: long"))
