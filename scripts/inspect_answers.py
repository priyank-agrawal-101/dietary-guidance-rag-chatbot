"""Run approved active-corpus answer cases; never downloads or publishes a corpus."""

import argparse
import json
from contextlib import nullcontext
from hashlib import sha256
from pathlib import Path

from dietary_rag.answering.adapters import ExtractiveGenerator
from dietary_rag.answering.contracts import AnswerModelUnavailable
from dietary_rag.answering.service import AnswerService
from dietary_rag.corpus.lifecycle import LifecycleStore
from dietary_rag.corpus.registry import load_registry
from dietary_rag.ingestion.chunk_tokenizer import E5Tokenizer
from dietary_rag.policy.runtime import PolicyRuntime
from dietary_rag.policy.service import PolicyService
from dietary_rag.providers.embeddings import BGETextProcessor
from dietary_rag.retrieval.resolution import DocumentResolver
from dietary_rag.settings import load_settings


class ReviewGenerator:
    """Explicit fault injection for reviewed error/repair examples, never the CLI default."""

    def __init__(self, mode):
        self.mode, self.calls = mode, 0

    def generate(self, request):
        self.calls += 1
        if self.mode == "unavailable":
            raise AnswerModelUnavailable("injected review outage")
        if self.mode == "timeout":
            raise TimeoutError("injected review timeout")
        draft = ExtractiveGenerator().generate(request).model_dump(mode="json")
        if self.mode == "invalid_numeric" or self.mode == "repaired" and self.calls == 1:
            claim = draft["document_answers"][0]["claims"][0]
            claim["claim_text"] = claim["claim_text"].replace("165", "999")
        return draft


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=Path("tests/fixtures/answering/review.json"))
    parser.add_argument("--output", type=Path, default=Path("data/staging/answering/review.json"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    settings = load_settings(Path("config/settings.yaml"), project_root=root, environ={})
    registry = load_registry(settings.source_registry)
    resolver = DocumentResolver(
        registry, json.loads((root / "config/document-aliases.json").read_text())
    )
    processor = BGETextProcessor(
        root / "data/staging/models/bge-small-en-v1.5",
        root / "config/embedding-bge-small-en-v1.5.json",
        settings.embedding,
    )
    tokenizer = E5Tokenizer(
        root / "data/staging/tokenizers/e5-base-v2", root / "config/tokenizer-e5-base-v2.json"
    )
    pin = LifecycleStore(settings.data_dir / "active").pin()
    rows = []
    with pin.open_request(registry, tokenizer, processor, resolver, settings.runtime) as request:
        service = AnswerService(
            PolicyRuntime(
                lambda: nullcontext(request),
                runtime=settings.runtime,
                policy=PolicyService(settings.policy),
            ),
            settings=settings.answering,
        )
        for case in json.loads(args.cases.read_text())["cases"]:
            service.generator = (
                ReviewGenerator(case["generator_mode"])
                if case.get("generator_mode")
                else ExtractiveGenerator()
            )
            report = service.inspect(case["request"]).model_dump()
            response = report["response"]
            actual = response.get("status", response.get("code"))
            text = " ".join(
                c["text"] for d in response.get("document_answers", []) for c in d["claims"]
            )
            passed = actual == case["expected_status"] and all(
                s.casefold() in text.casefold() for s in case.get("required_phrases", [])
            )
            if "expected_generation_attempts" in case:
                passed = (
                    passed
                    and report["inspection"]["generation_attempts"]
                    == case["expected_generation_attempts"]
                )
            if "expected_answer_documents" in case:
                passed = (
                    passed
                    and [d["document_id"] for d in response.get("document_answers", [])]
                    == case["expected_answer_documents"]
                )
            rows.append(
                {
                    "case_id": case["id"],
                    "expected_status": case["expected_status"],
                    "actual_status": actual,
                    "passed": passed,
                    **report,
                }
            )
            print(case["id"], actual, passed)
    output = {
        "version": "answer_review_v1",
        "release_id": pin.release.release_id,
        "corpus_snapshot_id": pin.release.corpus_snapshot_id,
        "generator": "extractive_v1",
        "case_file_sha256": sha256(args.cases.read_bytes()).hexdigest(),
        "review_date": "2026-11-12",
        "verifier": "extractive_semantic_v1",
        "cases_passed": sum(r["passed"] for r in rows),
        "cases_total": len(rows),
        "results": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    return 0 if all(r["passed"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
