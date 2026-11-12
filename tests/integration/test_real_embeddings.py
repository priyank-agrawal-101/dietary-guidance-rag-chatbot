"""Optional real pinned-model smoke checks; tests never download artifacts."""

import math
from pathlib import Path

import pytest

from dietary_rag.providers.embeddings import BGEEmbedder
from dietary_rag.settings import EmbeddingSettings


@pytest.fixture(scope="module")
def real_embedder():
    pytest.importorskip("sentence_transformers", reason="install requirements/embeddings.lock")
    root = Path(__file__).resolve().parents[2]
    cache = root / "data/staging/models/bge-small-en-v1.5"
    if not (cache / "model.safetensors").exists():
        pytest.skip("run prepare-embeddings explicitly; tests never download")
    return BGEEmbedder(cache, root / "config/embedding-bge-small-en-v1.5.json", EmbeddingSettings())


def test_real_model_offline_query_and_passage_have_normalized_384_vectors(
    real_embedder, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("embedding inference attempted network")

    monkeypatch.setattr("socket.socket", forbidden)
    query = real_embedder.embed_query("How should I refrigerate perishable food?")
    passages = real_embedder.encode(
        [
            "Document: Food safety\nAudience: household consumers\n\nRefrigerate perishable food within two hours.",
            "Document: Food choices\nAudience: household consumers\n\nChoose a variety of vegetables.",
        ]
    )
    assert len(query) == 384 and math.isclose(sum(v * v for v in query), 1.0, abs_tol=1e-5)
    # Smoke check for functional query/passage representations, not corpus accuracy.
    assert sum(a * b for a, b in zip(query, passages[0], strict=True)) > sum(
        a * b for a, b in zip(query, passages[1], strict=True)
    )


def test_real_model_rejects_overlimit_query_and_preserves_512_token_boundary(real_embedder):
    assert real_embedder.count("food " * 510) == 512
    assert len(real_embedder.encode(["food " * 510])[0]) == 384
    with pytest.raises(ValueError, match="truncation forbidden"):
        real_embedder.embed_query("food " * 512)
