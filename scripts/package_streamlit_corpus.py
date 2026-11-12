"""Package reviewed canonical text and frozen questions, never portable binary indexes."""

import argparse
import gzip
import io
import json
import sys
import tarfile
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dietary_rag.corpus.lifecycle import LifecycleStore  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle-dir", type=Path, default=ROOT / "data/active")
    args = parser.parse_args()
    store = LifecycleStore(args.lifecycle_dir)
    pin = store.pin()
    snapshot = pin.release.snapshot_directory
    proof = store.releases / pin.release.release_id
    files = {
        "evidence.sqlite": snapshot / "evidence.sqlite",
        "manifest.json": snapshot / "manifest.json",
    }
    files.update(
        {
            f"proof/{name}": proof / name
            for name in ("freeze.json", "regressions.json", "heldout.json")
        }
    )
    destination = ROOT / "deployment/artifacts/corpus.tar.gz"
    destination.parent.mkdir(parents=True, exist_ok=True)
    members = {}
    with (
        destination.open("wb") as output,
        gzip.GzipFile(fileobj=output, mode="wb", mtime=0, filename="") as compressed,
    ):
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for name, path in sorted(files.items()):
                data = path.read_bytes()
                members[name] = {"sha256": sha256(data).hexdigest(), "bytes": len(data)}
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o600
                archive.addfile(info, io.BytesIO(data))
    data = destination.read_bytes()
    receipt = {
        "version": "streamlit_corpus_v1",
        "archive": "deployment/artifacts/corpus.tar.gz",
        "sha256": sha256(data).hexdigest(),
        "bytes": len(data),
        "chunk_snapshot_id": json.loads(files["manifest.json"].read_text())["chunk_snapshot"][
            "snapshot_id"
        ],
        "members": members,
        "scope": "Learning project; reviewed canonical text only. Rebuild native index and evaluate before activation.",
    }
    (ROOT / "config/deployment-artifacts.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Packaged {len(data):,} bytes; {receipt['sha256']}")


if __name__ == "__main__":
    main()
