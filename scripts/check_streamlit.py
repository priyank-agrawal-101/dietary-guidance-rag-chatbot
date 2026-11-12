"""Cold/warm bootstrap and frozen answer evaluation on the deployment host (no Groq)."""

import argparse
import json
import platform
import resource
import sys
import time
from contextlib import nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from inspect_answers import ReviewGenerator  # noqa: E402

from dietary_rag.answering.adapters import ExtractiveGenerator  # noqa: E402
from dietary_rag.answering.service import AnswerService  # noqa: E402
from dietary_rag.corpus.registry import load_registry  # noqa: E402
from dietary_rag.deployment.bootstrap import bootstrap_identity, ensure_runtime  # noqa: E402
from dietary_rag.evaluation.checks import audit_indexed_coverage, check_answer  # noqa: E402
from dietary_rag.policy.runtime import PolicyRuntime  # noqa: E402
from dietary_rag.policy.service import PolicyService  # noqa: E402
from dietary_rag.settings import load_settings  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".streamlit-runtime")
    parser.add_argument("--model-seed", type=Path)
    parser.add_argument("--tokenizer-seed", type=Path)
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".streamlit-runtime/deployment-check.json"
    )
    args = parser.parse_args()
    start = time.monotonic()
    settings = load_settings(ROOT / "config/settings.yaml", project_root=ROOT, environ={})
    registry = load_registry(settings.source_registry)
    active = ensure_runtime(
        ROOT,
        settings,
        registry,
        args.cache_dir,
        model_seed=args.model_seed,
        tokenizer_seed=args.tokenizer_seed,
    )
    cold_seconds = time.monotonic() - start
    start = time.monotonic()
    warm = ensure_runtime(ROOT, settings, registry, args.cache_dir)
    assert warm.store.pin().release.release_id == active.store.pin().release.release_id
    warm_seconds = time.monotonic() - start
    dataset = json.loads((ROOT / "config/evaluation-cases.json").read_text())
    rows = []
    with active.open_request() as request:
        assert (
            request.retriever.manifest["chunk_snapshot"]["snapshot_id"]
            == dataset["chunk_snapshot_id"]
        )
        coverage = audit_indexed_coverage(registry, request.retriever.chunks)
        service = AnswerService(
            PolicyRuntime(
                lambda: nullcontext(request),
                runtime=settings.runtime,
                policy=PolicyService(settings.policy),
            ),
            settings=settings.answering,
        )
        for case in dataset["cases"]:
            service.generator = (
                ReviewGenerator(case["generator_mode"])
                if case.get("generator_mode")
                else ExtractiveGenerator()
            )
            inspection = service.inspect(case["request"]).model_dump()
            checks = check_answer(case, inspection, request.retriever.chunks)
            rows.append({"case_id": case["id"], "passed": all(checks.values()), "checks": checks})
        service.generator = ExtractiveGenerator()
        smoke = []
        for question in (
            "How long can hard-cooked eggs be kept in the refrigerator?",
            "How do microwave wattage and cooking time affect reheating leftovers safely?",
            "Do you know about President of US?",
        ):
            result = service.answer({"question": question})
            smoke.append(
                {
                    "question": question,
                    "status": getattr(result, "status", getattr(result, "code", "unknown")),
                }
            )
        assert [r["status"] for r in smoke] == ["answered", "answered", "out_of_scope"]
    selection = json.loads((active.store.directory.parent / "bootstrap-report.json").read_text())[
        "selection"
    ]
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report = {
        "version": "streamlit_deployment_check_v1",
        "host": platform.platform(),
        "python": platform.python_version(),
        "bootstrap_identity": bootstrap_identity(ROOT),
        "startup_seconds": cold_seconds,
        "warm_start_seconds": warm_seconds,
        "peak_rss_mib": peak / (1024 * 1024 if platform.system() == "Darwin" else 1024),
        "retrieval_strategy": selection["selected_strategy"],
        "retrieval_gates": {
            r["strategy"]: r["passes_selection_gates"] for r in selection["strategy_reports"]
        },
        "cases_total": len(rows),
        "cases_passed": sum(r["passed"] for r in rows),
        "passed": all(r["passed"] for r in rows) and coverage["passes_quota_and_eligibility"],
        "coverage": coverage,
        "cases": rows,
        "smoke": smoke,
        "scope": "Automated host reproduction for a Human–AI assisted learning project; not independent human or live-user approval.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k not in ("cases", "coverage")}, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
