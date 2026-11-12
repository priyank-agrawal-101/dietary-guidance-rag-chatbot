"""Pinned offline BGE-small inference; downloads happen only through preparation."""

import hashlib
import json
import math
import re
import tempfile
from importlib.metadata import version
from pathlib import Path
from urllib.request import urlopen

from dietary_rag.corpus.identity import sha256_bytes, stable_id

MODEL = "BAAI/bge-small-en-v1.5"
REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
FILES = frozenset(
    {
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "vocab.txt",
        "sentence_bert_config.json",
        "modules.json",
        "1_Pooling/config.json",
        "model.safetensors",
    }
)


def load_manifest(path):
    manifest = json.loads(Path(path).read_text())
    expected = {
        "model_name": MODEL,
        "revision": REVISION,
        "license": "MIT",
        "dimensions": 384,
        "max_tokens": 512,
        "pooling": "cls",
        "normalize": True,
        "metric": "cosine",
    }
    if any(manifest.get(k) != v for k, v in expected.items()):
        raise ValueError("unsupported embedding model/configuration")
    if set(manifest.get("files", {})) != FILES:
        raise ValueError("embedding manifest requires the exact pinned artifact set")
    for item in manifest["files"].values():
        if (
            set(item) != {"sha256", "size"}
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
            or type(item["size"]) is not int
            or not 0 < item["size"] <= 200_000_000
        ):
            raise ValueError("invalid embedding artifact hash/size")
    return manifest


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_artifacts(directory, manifest):
    root = Path(directory)
    for name, item in manifest["files"].items():
        path = root / name
        if not path.is_file() or path.stat().st_size != item["size"]:
            raise ValueError("embedding cache missing/incomplete; run prepare-embeddings")
        if file_hash(path) != item["sha256"]:
            raise ValueError("embedding cache hash differs from pinned manifest")
    # No unverified auxiliary files may influence Sentence Transformers loading.
    if {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} != FILES:
        raise ValueError("embedding cache contains unpinned auxiliary files")
    config = json.loads((root / "config.json").read_text())
    pooling = json.loads((root / "1_Pooling/config.json").read_text())
    modules = json.loads((root / "modules.json").read_text())
    limits = (
        config["max_position_embeddings"],
        json.loads((root / "sentence_bert_config.json").read_text())["max_seq_length"],
        json.loads((root / "tokenizer_config.json").read_text())["model_max_length"],
    )
    if config["hidden_size"] != 384 or any(limit != 512 for limit in limits):
        raise ValueError("embedding model/tokenizer limits or dimensions disagree")
    if (
        pooling["word_embedding_dimension"] != 384
        or not pooling["pooling_mode_cls_token"]
        or any(
            v
            for k, v in pooling.items()
            if k.startswith("pooling_mode_") and k != "pooling_mode_cls_token"
        )
        or [(m["path"], m["type"]) for m in modules]
        != [
            ("", "sentence_transformers.models.Transformer"),
            ("1_Pooling", "sentence_transformers.models.Pooling"),
            ("2_Normalize", "sentence_transformers.models.Normalize"),
        ]
    ):
        raise ValueError("unsupported embedding modules/pooling")


def prepare_embeddings(manifest_path, directory):
    manifest = load_manifest(manifest_path)
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    for name, item in manifest["files"].items():
        target = root / name
        if (
            target.is_file()
            and target.stat().st_size == item["size"]
            and file_hash(target) == item["sha256"]
        ):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            url = f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}"
            with (
                urlopen(url, timeout=60) as response,
                tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream,
            ):
                temporary = Path(stream.name)
                if not response.url.startswith("https://"):
                    raise ValueError("embedding download requires HTTPS")
                total, digest = 0, hashlib.sha256()
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    total += len(block)
                    if total > item["size"]:
                        raise ValueError("embedding download exceeds pinned size")
                    digest.update(block)
                    stream.write(block)
            if total != item["size"] or digest.hexdigest() != item["sha256"]:
                raise ValueError("embedding download hash/size mismatch")
            temporary.replace(target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    verify_artifacts(root, manifest)
    return manifest


def passage_input(chunk):
    if not chunk.embedding_text.startswith("passage: "):
        raise ValueError("historical chunk passage prefix missing")
    return chunk.embedding_text.removeprefix("passage: ")


def validate_vectors(vectors, count):
    if len(vectors) != count:
        raise ValueError("embedding result count mismatch")
    for vector in vectors:
        if len(vector) != 384 or not all(math.isfinite(v) for v in vector):
            raise ValueError("embedding dimensions/nonfinite values invalid")
        if not math.isclose(sum(v * v for v in vector), 1.0, abs_tol=1e-5):
            raise ValueError("embedding vectors must be normalized")


class BGETextProcessor:
    """Verified inputs/configuration without importing or loading embedding weights."""

    def __init__(self, directory, manifest_path, settings):
        manifest = load_manifest(manifest_path)
        if settings.model_name != MODEL or settings.revision != REVISION:
            raise ValueError("configured embedding model/revision differs from pinned model")
        verify_artifacts(directory, manifest)
        from tokenizers import Tokenizer

        self.directory, self.manifest_path, self.settings = directory, manifest_path, settings
        self.tokenizer = Tokenizer.from_file(str(Path(directory) / "tokenizer.json"))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.metadata = {
            **manifest,
            "preprocessing": "bge_v1_remove_e5_passage_prefix",
            "query_prefix": QUERY_PREFIX,
            "device": "cpu",
            "dtype": "float32",
            "dependencies": {
                p: version(p)
                for p in (
                    "sentence-transformers",
                    "transformers",
                    "torch",
                    "tokenizers",
                    "numpy",
                    "chromadb",
                )
            },
        }
        self.configuration_id = stable_id("embedding_config", self.metadata)

    def count(self, text):
        return len(self.tokenizer.encode(text, add_special_tokens=True).ids)

    def input_record(self, chunk):
        text = passage_input(chunk)
        count = self.count(text)
        if count > 512:
            raise ValueError("BGE complete passage exceeds 512 tokens; reviewed rebuild required")
        return {
            "chunk_id": chunk.chunk_id,
            "input_text": text,
            "input_sha256": sha256_bytes(text.encode()),
            "token_count": count,
        }


class BGEEmbedder(BGETextProcessor):
    def __init__(self, directory, manifest_path, settings):
        super().__init__(directory, manifest_path, settings)
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(
            str(Path(directory).resolve()),
            device="cpu",
            local_files_only=True,
            trust_remote_code=False,
            model_kwargs={"use_safetensors": True},
        )
        dimension = getattr(
            self.model, "get_embedding_dimension", self.model.get_sentence_embedding_dimension
        )()
        if dimension != 384 or self.model.max_seq_length != 512:
            raise ValueError("loaded embedding model differs from pinned configuration")

    def encode(self, texts):
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("embedding requires nonempty inputs")
        counts = [self.count(text) for text in texts]
        if any(count > 512 for count in counts):
            raise ValueError("BGE complete input exceeds 512 tokens; truncation forbidden")
        # Check the model's actual preprocessing too, before any inference occurs.
        for start in range(0, len(texts), 32):
            preprocess = getattr(self.model, "preprocess", self.model.tokenize)
            batch = preprocess(texts[start : start + 32])
            actual = batch["attention_mask"].sum(dim=1).tolist()
            if actual != counts[start : start + 32]:
                raise ValueError("model tokenization differs from verified complete input")
        vectors = self.model.encode(
            texts,
            batch_size=32,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        ).tolist()
        validate_vectors(vectors, len(texts))
        return vectors

    def embed_query(self, question):
        if not question.strip() or question.startswith(QUERY_PREFIX):
            raise ValueError("query must be nonempty and must not include the model instruction")
        return self.encode([QUERY_PREFIX + question])[0]
