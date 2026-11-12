"""Fail-closed milestone release gate, distinct from development activation."""

import json
import shutil
import tempfile
from hashlib import sha256
from pathlib import Path

from dietary_rag.evaluation.checks import audit_indexed_coverage, check_answer

TARGETS = {
    "structural_and_policy": 1.0,
    "required_conditions": 0.95,
    "human_grounding": 0.95,
    "human_critical_errors": 0,
}


def implementation_hash(root):
    root = Path(root)
    paths = [root / "pyproject.toml"]
    for directory in ("src", "scripts", "deploy", "web", "requirements", "config"):
        paths.extend(
            p
            for p in (root / directory).rglob("*")
            if p.is_file()
            and "__pycache__" not in p.parts
            and p.suffix not in (".pyc", ".log")
            and "node_modules" not in p.parts
            and not any(part.endswith(".egg-info") for part in p.parts)
        )
    payload = {str(p.relative_to(root)): sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def release_checks(
    dataset_bytes,
    results_bytes,
    human,
    reproduction,
    request,
    *,
    implementation_id=None,
    learning_project=False,
):
    dataset, results = json.loads(dataset_bytes), json.loads(results_bytes)
    cases = {c["id"]: c for c in dataset["cases"]}
    rows = results["results"]
    complete = len(rows) == len(cases) and {r["case_id"] for r in rows} == set(cases)
    checked = [
        check_answer(cases[r["case_id"]], r, request.retriever.chunks)
        for r in rows
        if r["case_id"] in cases
    ]
    human_rows = human.get("cases", [])
    complete_verdicts = (
        len(human_rows) == len(cases)
        and {r.get("case_id") for r in human_rows} == set(cases)
        and all(
            type(r.get(k)) is bool
            for r in human_rows
            for k in ("grounded", "conditions_complete", "policy_safe", "critical_error")
        )
    )
    review_kind = human.get("review_type", "independent_human")
    authorized_learning = (
        learning_project
        and review_kind == "human_ai_assisted_learning"
        and human.get("acceptance_scope") == "learning_project_only"
        and human.get("independent_human_review") is False
        and human.get("user_authorization", {}).get("authorized_by") == "user"
        and bool(human.get("user_authorization", {}).get("statement"))
    )
    human_complete = complete_verdicts and (
        authorized_learning if learning_project else review_kind == "independent_human"
    )

    coverage = audit_indexed_coverage(request.retriever.registry, request.retriever.chunks)

    def rate(key):
        return sum(r.get(key) is True for r in human_rows) / len(cases) if cases else 0

    checks = {
        "prespecified_targets": dataset.get("acceptance_targets") == TARGETS,
        "dataset_bound": results.get("case_file_sha256") == sha256(dataset_bytes).hexdigest(),
        "snapshot_bound": results.get("release_id") == request.pin.release.release_id
        and results.get("corpus_snapshot_id") == request.pin.release.corpus_snapshot_id,
        "complete_suite": complete and 60 <= len(cases) <= 80,
        "structural_and_policy": complete
        and all(all(v for k, v in c.items() if k != "required_conditions") for c in checked),
        "required_conditions": complete
        and sum(c["required_conditions"] for c in checked) / len(cases)
        >= TARGETS["required_conditions"],
        "indexed_coverage": coverage["passes_quota_and_eligibility"],
        "human_result_bound": human.get("results_sha256") == sha256(results_bytes).hexdigest(),
        "human_review_complete": human_complete
        and bool(str(human.get("reviewer") or "").strip())
        and human.get("status") == "completed",
        "human_grounding_and_conditions": human_complete
        and rate("grounded") >= TARGETS["human_grounding"]
        and rate("conditions_complete") >= TARGETS["required_conditions"],
        "human_policy_and_critical_errors": human_complete
        and rate("policy_safe") == 1
        and not any(r.get("critical_error") for r in human_rows),
        "reproduction_matches_implementation": bool(implementation_id)
        and reproduction.get("implementation_sha256") == implementation_id,
        "clean_reproduction": reproduction.get("status") == "passed"
        and reproduction.get("isolated_environment") is True
        and reproduction.get("tests_passed") is True,
    }
    if learning_project:
        checks["user_authorized_learning_scope"] = authorized_learning
        checks = {
            key.replace("human_", "human_ai_assisted_"): value for key, value in checks.items()
        }
    return {
        "version": "milestone_release_gate_v1",
        "release_approved": all(checks.values()),
        "release_scope": "learning_project_only"
        if learning_project
        else "independent_human_review",
        "review_type": review_kind,
        "independent_human_review": review_kind == "independent_human" and human_complete,
        "review_summary": {
            "cases_total": len(cases),
            "grounded_cases": sum(r.get("grounded") is True for r in human_rows),
            "conditions_complete_cases": sum(
                r.get("conditions_complete") is True for r in human_rows
            ),
            "policy_safe_cases": sum(r.get("policy_safe") is True for r in human_rows),
            "critical_errors": sum(r.get("critical_error") is True for r in human_rows),
        },
        "checks": checks,
        "blockers": [k for k, v in checks.items() if not v],
        "coverage": coverage,
        "release_id": request.pin.release.release_id,
        "corpus_snapshot_id": request.pin.release.corpus_snapshot_id,
        "dataset_sha256": sha256(dataset_bytes).hexdigest(),
        "results_sha256": sha256(results_bytes).hexdigest(),
    }


def publish_bundle(destination, report, *, snapshot, files):
    """Publish a checksummed immutable local bundle only after all release checks pass.

    Model weights/raw sources stay separately managed. Runtime snapshot and retained
    proof are included; installation revalidates/activates at its new absolute path.
    """
    if not report["release_approved"] or not all(report["checks"].values()):
        raise ValueError("milestone release gates failed; publication blocked")
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError("release bundle already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".release-", dir=destination.parent))
    try:
        shutil.copytree(snapshot, temporary / "snapshot")
        for name, path in files.items():
            target = temporary / name
            if target.resolve().is_relative_to(temporary.resolve()) is False:
                raise ValueError("bundle path escapes destination")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        (temporary / "release-report.json").write_text(json.dumps(report, indent=2) + "\n")
        manifest = {
            str(p.relative_to(temporary)): sha256(p.read_bytes()).hexdigest()
            for p in sorted(temporary.rglob("*"))
            if p.is_file()
        }
        (temporary / "checksums.json").write_text(json.dumps(manifest, indent=2) + "\n")
        temporary.rename(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
