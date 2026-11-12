"""Small offline BM25 diagnostic for chunk boundaries, not a dense E5 benchmark."""

import json
import math
import re
from collections import Counter
from pathlib import Path

from dietary_rag.corpus.identity import sha256_bytes
from dietary_rag.ingestion.chunk_contracts import ChunkBuild


def terms(text):
    return re.findall(r"[a-z0-9]+", text.casefold())


def evaluate(builds, cases, *, k=5):
    chunks = sorted((c for b in builds for c in b.chunks), key=lambda c: c.chunk_id)
    if not chunks or not cases or k <= 0:
        raise ValueError("evaluation requires chunks, cases and positive k")
    if any(b.role != "consumer" or b.state != "ready" for b in builds):
        raise ValueError("evaluation accepts ready consumer evidence only")
    frequencies = [Counter(terms(c.embedding_text)) for c in chunks]
    lengths = [sum(f.values()) for f in frequencies]
    average = sum(lengths) / len(lengths)
    df = Counter(t for f in frequencies for t in f)
    results = []
    for case in cases:
        expected = [c for c in chunks if matches(c, case)]
        if not expected:
            raise ValueError(f"case {case['id']} lacks attributable expected evidence")
        query = set(terms(case["question"]))
        scores = []
        for c, f, length in zip(chunks, frequencies, lengths, strict=True):
            score = sum(
                math.log(1 + (len(chunks) - df[t] + 0.5) / (df[t] + 0.5))
                * f[t]
                * 2.2
                / (f[t] + 1.2 * (0.25 + 0.75 * length / average))
                for t in query
                if f[t]
            )
            scores.append((score, c))
        ranked = sorted(scores, key=lambda p: (-p[0], p[1].chunk_id))[:k]
        hit = any(matches(c, case) for score, c in ranked)
        results.append(
            {
                "case_id": case["id"],
                "question": case["question"],
                "complete_evidence_hit": hit,
                "expected_chunk_ids": [c.chunk_id for c in expected],
                "top_chunks": [
                    {
                        "chunk_id": c.chunk_id,
                        "document_id": c.document_id,
                        "score": round(score, 6),
                        "matches": matches(c, case),
                    }
                    for score, c in ranked
                ],
            }
        )
    return {
        "target_tokens": builds[0].target_tokens,
        "chunks": len(chunks),
        "k": k,
        "cases": len(cases),
        "complete_evidence_hits": sum(r["complete_evidence_hit"] for r in results),
        "complete_evidence_hit_rate": sum(r["complete_evidence_hit"] for r in results) / len(cases),
        "results": results,
    }


def matches(chunk, case):
    return chunk.document_id in case["document_ids"] and all(
        value.casefold() in chunk.text.casefold() for value in case["required_phrases"]
    )


def compare_reports(baseline, candidate, cases_path, project_root):
    def read(report_path):
        report = json.loads(Path(report_path).read_text())
        if report["status"] != "reviewed_staging_chunks" or report["role"] != "consumer":
            raise ValueError("comparison requires successful consumer chunk reports")
        builds = []
        for row in report["results"]:
            path = Path(row["output"])
            if not path.is_absolute():
                path = project_root / path
            build = ChunkBuild.model_validate_json(path.read_text())
            if build.build_id != row["build_id"]:
                raise ValueError("evaluation artifact identity differs from report")
            builds.append(build)
        if sorted(b.build_id for b in builds) != report["snapshot"]["build_ids"]:
            raise ValueError("evaluation builds differ from snapshot manifest")
        return builds

    first, second = read(baseline), read(candidate)
    if sorted(b.normalization_id for b in first) != sorted(b.normalization_id for b in second):
        raise ValueError("comparison requires identical reviewed source evidence")
    if any(
        b.tokenizer != first[0].tokenizer or b.overlap_tokens != first[0].overlap_tokens
        for b in [*first, *second]
    ):
        raise ValueError("comparison requires identical tokenizer and overlap")
    data = Path(cases_path).read_bytes()
    cases = json.loads(data)["cases"]
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("duplicate evaluation case")
    return {
        "method": "offline_bm25",
        "parameters": {"k1": 1.2, "b": 0.75, "k": 5},
        "limitation": "Small authored lexical diagnostic; not dense E5 retrieval, answer accuracy or a representative benchmark.",
        "cases_sha256": sha256_bytes(data),
        "baseline": evaluate(first, cases),
        "candidate": evaluate(second, cases),
        "default_target_tokens": 350,
        "decision": "Retain 350; run dense E5 and broader held-out retrieval evaluation in Phase 3 before changing defaults.",
    }
