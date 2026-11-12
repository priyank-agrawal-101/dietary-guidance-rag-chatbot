#!/usr/bin/env python3
"""Display stored Chroma vectors alongside their canonical chunk evidence."""

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path

from dietary_rag.corpus.chunk_repository import ChunkRepository
from dietary_rag.corpus.index_snapshot import chroma_client, close_chroma

ROOT = Path(__file__).resolve().parents[1]


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def rooted(path):
    return path if path.is_absolute() else ROOT / path


def inspect(snapshot_dir, limit, document_id=None):
    """Read existing vectors; never embed text or create a collection."""
    manifest = json.loads((snapshot_dir / "manifest.json").read_text())
    if not (snapshot_dir / "chroma/chroma.sqlite3").is_file():
        raise ValueError("Chroma index missing at the selected snapshot")
    if not (snapshot_dir / "evidence.sqlite").is_file():
        raise ValueError("canonical evidence database missing")
    client = chroma_client(snapshot_dir / "chroma")
    try:
        collection = client.get_collection(manifest["collection"], embedding_function=None)
        if collection.metadata != {
            "corpus_version": manifest["snapshot_id"],
            "embedding_configuration_id": manifest["embedding_configuration_id"],
        }:
            raise ValueError("collection and snapshot configurations differ")
        results = collection.get(
            limit=limit,
            where={"document_id": document_id} if document_id else None,
            include=["embeddings", "metadatas"],
        )
        samples = []
        with ChunkRepository(snapshot_dir / "evidence.sqlite", read_only=True) as evidence:
            for identifier, vector, metadata in zip(
                results["ids"], results["embeddings"], results["metadatas"], strict=True
            ):
                if identifier not in manifest["chunk_ids"]:
                    raise ValueError("stored vector is outside the selected snapshot")
                resolved = evidence.resolve_chunk(identifier)
                chunk, document = resolved["chunk"], resolved["build"].document
                if metadata["document_id"] != chunk.document_id:
                    raise ValueError("vector metadata differs from canonical chunk")
                values = vector.tolist()
                samples.append(
                    {
                        "chunk_id": identifier,
                        "document_id": chunk.document_id,
                        "document_name": document.document_name,
                        "chunk_type": chunk.chunk_type,
                        "text": chunk.text,
                        "source_url": str(document.source_url),
                        "pdf_pages": sorted(
                            {
                                location.physical_page
                                for location in chunk.source_locations
                                if location.physical_page is not None
                            }
                        ),
                        "metadata": metadata,
                        "dimensions": len(values),
                        "l2_norm": math.sqrt(sum(value * value for value in values)),
                        "embedding": values,
                    }
                )
        return {
            "snapshot_id": manifest["snapshot_id"],
            "model": manifest["embedding"]["model_name"],
            "collection": collection.name,
            "stored_vectors": collection.count(),
            "samples": samples,
        }
    finally:
        close_chroma(client)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--snapshot-dir", type=Path, help="explicit paired snapshot directory")
    selection.add_argument(
        "--build-report",
        type=Path,
        default=Path("data/staging/index-build-report.json"),
        help="report selecting the snapshot (default: current project index build report)",
    )
    parser.add_argument(
        "--limit", type=positive_int, default=3, help="number of chunks (default: 3)"
    )
    parser.add_argument("--document-id", help="show only chunks from this document")
    parser.add_argument(
        "--dimensions", type=positive_int, default=12, help="vector values to preview (default: 12)"
    )
    parser.add_argument(
        "--full-vector", action="store_true", help="print every stored vector value"
    )
    parser.add_argument(
        "--json", action="store_true", help="JSON output with full text, metadata and vectors"
    )
    args = parser.parse_args(argv)
    try:
        if args.snapshot_dir:
            snapshot = rooted(args.snapshot_dir)
        else:
            report = json.loads(rooted(args.build_report).read_text())
            snapshot = rooted(Path(report["snapshot_dir"]))
        report = inspect(snapshot, args.limit, args.document_id)
        if args.json:
            print(json.dumps(report, indent=2, allow_nan=False))
        else:
            print(f"Snapshot: {report['snapshot_id']}")
            print(f"Model: {report['model']}")
            print(f"Collection: {report['collection']} ({report['stored_vectors']} stored vectors)")
            if not report["samples"]:
                print("No stored chunks match the requested document.")
            for index, sample in enumerate(report["samples"], start=1):
                print(f"\n[{index}] {sample['chunk_id']}")
                print(f"Document: {sample['document_id']} — {sample['document_name']}")
                print(f"Type: {sample['chunk_type']}; PDF pages: {sample['pdf_pages'] or 'n/a'}")
                print(f"Source: {sample['source_url']}")
                preview = sample["text"][:400]
                print(f"Chunk text: {preview}{'…' if len(sample['text']) > 400 else ''}")
                vector = sample["embedding"]
                shown = vector if args.full_vector else vector[: args.dimensions]
                print(
                    f"Embedding: {sample['dimensions']} dimensions; L2 norm={sample['l2_norm']:.6f}"
                )
                print(
                    f"Values ({len(shown)}/{len(vector)}): [{', '.join(f'{v:.8f}' for v in shown)}]"
                )
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, sqlite3.Error, ImportError) as exc:
        print(f"Cannot inspect stored embeddings: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
