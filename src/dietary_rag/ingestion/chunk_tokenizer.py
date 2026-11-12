"""Pinned, hash-verified E5 token counting; no weights or implicit downloads."""

import json
import re
from pathlib import Path
from urllib.request import urlopen

from dietary_rag.corpus.identity import sha256_bytes

FILES = frozenset(
    {
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "vocab.txt",
        "config.json",
        "sentence_bert_config.json",
    }
)


def load_manifest(manifest_path):
    manifest = json.loads(Path(manifest_path).read_text())
    if (
        manifest["model_name"] != "intfloat/e5-base-v2"
        or set(manifest["files"]) != FILES
        or not re.fullmatch(r"[0-9a-f]{40}", manifest["revision"])
        or manifest["max_tokens"] != 512
        or any(not re.fullmatch(r"[0-9a-f]{64}", v) for v in manifest["files"].values())
    ):
        raise ValueError("unsupported or unpinned tokenizer manifest")
    return manifest


def prepare_tokenizer(manifest_path, output_dir):
    manifest = load_manifest(manifest_path)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    for name, digest in manifest["files"].items():
        target = root / name
        if target.exists() and sha256_bytes(target.read_bytes()) == digest:
            continue
        url = (
            f"https://huggingface.co/{manifest['model_name']}/resolve/{manifest['revision']}/{name}"
        )
        with urlopen(url, timeout=30) as response:
            if not response.url.startswith("https://"):
                raise ValueError("tokenizer redirect requires HTTPS")
            data = response.read(10_000_001)
        if len(data) > 10_000_000 or sha256_bytes(data) != digest:
            raise ValueError("tokenizer download hash/size mismatch")
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(target)
    return manifest


class E5Tokenizer:
    def __init__(self, directory, manifest_path):
        from tokenizers import Tokenizer, __version__

        manifest = load_manifest(manifest_path)
        root = Path(directory)
        for name, digest in manifest["files"].items():
            path = root / name
            if not path.exists():
                raise ValueError("tokenizer cache missing; run prepare-tokenizer")
            if sha256_bytes(path.read_bytes()) != digest:
                raise ValueError("tokenizer cache hash differs from pinned manifest")
        limits = [
            json.loads((root / "config.json").read_text())["max_position_embeddings"],
            json.loads((root / "sentence_bert_config.json").read_text())["max_seq_length"],
            json.loads((root / "tokenizer_config.json").read_text())["model_max_length"],
        ]
        if any(v != manifest["max_tokens"] for v in limits):
            raise ValueError("pinned tokenizer/model input limits disagree")
        self.limit = min(limits)
        self.metadata = {**manifest, "tokenizers_version": __version__}
        self.tokenizer = Tokenizer.from_file(str(root / "tokenizer.json"))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self._counts = {}

    def count(self, text, *, special_tokens=True):
        key = (text, special_tokens)
        if key not in self._counts:
            if len(self._counts) >= 4096:
                self._counts.clear()
            self._counts[key] = len(
                self.tokenizer.encode(text, add_special_tokens=special_tokens).ids
            )
        return self._counts[key]
