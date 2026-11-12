"""Score a frozen suite on one validated active pin without network/provider calls."""

import argparse
import json
from collections import defaultdict
from contextlib import nullcontext
from hashlib import sha256
from pathlib import Path

from inspect_answers import ReviewGenerator

from dietary_rag.answering.adapters import ExtractiveGenerator
from dietary_rag.answering.service import AnswerService
from dietary_rag.api.runtime import ActiveRuntime
from dietary_rag.corpus.registry import load_registry
from dietary_rag.evaluation.checks import audit_indexed_coverage, check_answer
from dietary_rag.policy.runtime import PolicyRuntime
from dietary_rag.policy.service import PolicyService
from dietary_rag.settings import load_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("config/evaluation-cases.json"))
    parser.add_argument("--output", type=Path, default=Path("data/staging/evaluation/results.json"))
    args = parser.parse_args()
    raw = args.cases.read_bytes()
    dataset = json.loads(raw)
    cases = dataset["cases"]
    if not 60 <= len(cases) <= 80 or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("requires 60–80 unique frozen cases")
    root = Path(__file__).resolve().parents[1]
    settings = load_settings(root / "config/settings.yaml", project_root=root, environ={})
    registry = load_registry(settings.source_registry)
    active = ActiveRuntime(settings, registry, root)
    rows = []
    groups = defaultdict(list)
    with active.open_request() as request:
        service = AnswerService(
            PolicyRuntime(
                lambda: nullcontext(request),
                runtime=settings.runtime,
                policy=PolicyService(settings.policy),
            ),
            settings=settings.answering,
        )
        coverage = audit_indexed_coverage(registry, request.retriever.chunks)
        if (
            request.retriever.manifest["chunk_snapshot"]["snapshot_id"]
            != dataset["chunk_snapshot_id"]
        ):
            raise ValueError("frozen evidence belongs to a different chunk snapshot")
        for case in cases:
            if any(i not in request.retriever.chunks for i in case["expected_evidence_chunk_ids"]):
                raise ValueError(f"missing frozen evidence for {case['id']}")
        for case in cases:
            service.generator = (
                ReviewGenerator(case["generator_mode"])
                if case.get("generator_mode")
                else ExtractiveGenerator()
            )
            report = service.inspect(case["request"]).model_dump()
            checks = check_answer(case, report, request.retriever.chunks)
            formats = sorted(
                {
                    request.retriever.documents[i].format
                    for i in case["request"].get("scope", {}).get("document_ids", [])
                    if i in request.retriever.documents
                }
            ) or ["no-evidence"]
            row = {
                "case_id": case["id"],
                "category": case["category"],
                "formats": formats,
                "checks": checks,
                "passed": all(checks.values()),
                **report,
            }
            rows.append(row)
            layouts = sorted(
                {
                    request.retriever.chunks[i].chunk_type
                    for i in case["expected_evidence_chunk_ids"]
                }
            )
            row["evidence_types"] = layouts
            for group in [case["category"], *formats, *layouts]:
                groups[group].append(row)
            print(case["id"], row["passed"], [k for k, v in checks.items() if not v])
        identity = {
            "release_id": request.pin.release.release_id,
            "corpus_snapshot_id": request.pin.release.corpus_snapshot_id,
        }
    required_rate = sum(r["checks"]["required_conditions"] for r in rows) / len(rows)
    structural = all(
        all(v for k, v in r["checks"].items() if k != "required_conditions") for r in rows
    )
    output = {
        "version": "release_evaluation_results_v1",
        **identity,
        "case_file_sha256": sha256(raw).hexdigest(),
        "acceptance_targets": dataset["acceptance_targets"],
        "generator": "extractive_v1; explicit fault injection for dependency/repair cases",
        "cases_total": len(rows),
        "cases_passed": sum(r["passed"] for r in rows),
        "required_conditions_rate": required_rate,
        "automated_gates_pass": structural
        and required_rate >= dataset["acceptance_targets"]["required_conditions"]
        and coverage["passes_quota_and_eligibility"],
        "human_review_status": "pending",
        "release_approved": False,
        "coverage": coverage,
        "by_category_and_format": {
            k: {"total": len(v), "passed": sum(r["passed"] for r in v)}
            for k, v in sorted(groups.items())
        },
        "error_analysis": [
            {"case_id": r["case_id"], "failed_checks": [k for k, v in r["checks"].items() if not v]}
            for r in rows
            if not r["passed"]
        ],
        "results": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    review = {
        "results_sha256": sha256(args.output.read_bytes()).hexdigest(),
        "reviewer": None,
        "status": "pending",
        "rubric": "Check every claim, exact support, all conditions/units, source attribution, date/page, policy and gaps. Mark any unsafe or fabricated advice critical.",
        "cases": [
            {
                "case_id": r["case_id"],
                "grounded": None,
                "conditions_complete": None,
                "policy_safe": None,
                "critical_error": None,
                "notes": "",
            }
            for r in rows
        ],
    }
    args.output.with_suffix(".human-review.json").write_text(json.dumps(review, indent=2) + "\n")
    return 0 if output["automated_gates_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
