"""Aggregate telemetry and fail-closed release proof checks."""

import json
from hashlib import sha256
from types import SimpleNamespace as NS

import pytest

from dietary_rag.operations.metrics import CURRENT_METRICS, Metrics, stage
from dietary_rag.operations.release import TARGETS, publish_bundle, release_checks


def test_metrics_hold_only_aggregates_and_fixed_stage_labels():
    metrics = Metrics()
    token = CURRENT_METRICS.set(metrics)
    try:
        with stage("policy"):
            pass
        metrics.outcome(NS(status="out_of_scope"))
    finally:
        CURRENT_METRICS.reset(token)
    value = metrics.snapshot()
    assert value["chat_states"] == {"out_of_scope": 1}
    assert value["stages"]["policy"]["count"] == 1
    with pytest.raises(ValueError):
        metrics.observe("user supplied question", 1)


def test_failed_gate_cannot_publish(tmp_path):
    with pytest.raises(ValueError, match="publication blocked"):
        publish_bundle(
            tmp_path / "release", {"release_approved": False}, snapshot=tmp_path, files={}
        )
    assert not (tmp_path / "release").exists()


def test_forged_automated_success_and_pending_human_review_rejected(reviewed_registry):
    cases = [
        {
            "id": str(i),
            "expected_status": "answered",
            "required_phrases": ["165"],
            "forbidden_phrases": [],
            "request": {},
        }
        for i in range(60)
    ]
    dataset = json.dumps({"acceptance_targets": TARGETS, "cases": cases}).encode()
    results = json.dumps(
        {
            "case_file_sha256": sha256(dataset).hexdigest(),
            "release_id": "r",
            "corpus_snapshot_id": "s",
            "automated_gates_pass": True,
            "results": [
                {"case_id": str(i), "response": {"status": "not_covered"}} for i in range(60)
            ],
        }
    ).encode()
    request = NS(
        pin=NS(release=NS(release_id="r", corpus_snapshot_id="s")),
        retriever=NS(registry=reviewed_registry, chunks={}),
    )
    report = release_checks(dataset, results, {"status": "pending"}, {"status": "passed"}, request)
    assert not report["release_approved"]
    assert not report["checks"]["structural_and_policy"]
    assert not report["checks"]["human_review_complete"]
    assert not report["checks"]["indexed_coverage"]


def test_passing_bundle_has_checksums_and_cannot_overwrite(tmp_path):
    snapshot = tmp_path / "candidate"
    snapshot.mkdir()
    (snapshot / "manifest.json").write_text("{}")
    evidence = tmp_path / "proof.json"
    evidence.write_text('{"approved": true}')
    target = tmp_path / "published"
    report = {"release_approved": True, "checks": {"valid": True}}
    publish_bundle(target, report, snapshot=snapshot, files={"proof/review.json": evidence})
    manifest = json.loads((target / "checksums.json").read_text())
    assert manifest["proof/review.json"] == sha256(evidence.read_bytes()).hexdigest()
    assert (target / "snapshot/manifest.json").read_text() == "{}"
    with pytest.raises(FileExistsError):
        publish_bundle(target, report, snapshot=snapshot, files={})


def test_ai_assisted_review_requires_learning_scope_and_preserves_thresholds(
    reviewed_registry, monkeypatch
):
    import dietary_rag.operations.release as release_module

    monkeypatch.setattr(
        release_module, "audit_indexed_coverage", lambda *a: {"passes_quota_and_eligibility": True}
    )
    cases = [
        {
            "id": str(i),
            "expected_status": "out_of_scope",
            "required_phrases": [],
            "forbidden_phrases": [],
            "request": {},
        }
        for i in range(71)
    ]
    dataset = json.dumps({"acceptance_targets": TARGETS, "cases": cases}).encode()
    results = json.dumps(
        {
            "case_file_sha256": sha256(dataset).hexdigest(),
            "release_id": "r",
            "corpus_snapshot_id": "s",
            "results": [
                {"case_id": str(i), "response": {"status": "out_of_scope"}} for i in range(71)
            ],
        }
    ).encode()
    human = {
        "results_sha256": sha256(results).hexdigest(),
        "reviewer": "Codex (AI)",
        "status": "completed",
        "review_type": "human_ai_assisted_learning",
        "acceptance_scope": "learning_project_only",
        "independent_human_review": False,
        "user_authorization": {"authorized_by": "user", "statement": "Learning project only"},
        "cases": [
            {
                "case_id": str(i),
                "grounded": True,
                "conditions_complete": i >= 3,
                "policy_safe": True,
                "critical_error": False,
            }
            for i in range(71)
        ],
    }
    reproduction = {
        "status": "passed",
        "isolated_environment": True,
        "tests_passed": True,
        "implementation_sha256": "impl",
    }
    request = NS(
        pin=NS(release=NS(release_id="r", corpus_snapshot_id="s")),
        retriever=NS(registry=reviewed_registry, chunks={}),
    )

    def check(learning=True):
        return release_checks(
            dataset,
            results,
            human,
            reproduction,
            request,
            implementation_id="impl",
            learning_project=learning,
        )

    assert not check(False)["release_approved"]
    report = check()
    assert report["release_approved"]
    assert report["release_scope"] == "learning_project_only"
    assert report["independent_human_review"] is False
    assert report["review_summary"]["conditions_complete_cases"] == 68
    human["cases"][3]["conditions_complete"] = False
    assert not check()["release_approved"]
    human["cases"][3]["conditions_complete"] = True
    human["cases"][0]["critical_error"] = True
    assert not check()["release_approved"]
    human["cases"][0]["critical_error"] = False
    human["user_authorization"] = {}
    assert not check()["release_approved"]
