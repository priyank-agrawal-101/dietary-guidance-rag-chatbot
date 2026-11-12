"""Release diagnostics reject scope/citation faults and inflated coverage."""

import json
from pathlib import Path
from types import SimpleNamespace as NS

from dietary_rag.corpus.registry import load_registry
from dietary_rag.evaluation.checks import audit_indexed_coverage, check_answer


def test_alternate_assets_and_unknown_documents_do_not_meet_quota():
    registry = load_registry(Path("config/sources.yaml"))
    chunks = {str(i): NS(document_id="mn_storage_chart") for i in range(100)}
    chunks["bad"] = NS(document_id="unknown")
    audit = audit_indexed_coverage(registry, chunks)
    assert audit["eligible_indexed_family_count"] == 1
    assert not audit["passes_quota_and_eligibility"]
    assert audit["unexpected_indexed_documents"] == ["unknown"]


def test_fabricated_citation_and_scope_leak_fail():
    case = {
        "expected_status": "answered",
        "required_phrases": ["165"],
        "forbidden_phrases": ["999"],
        "request": {"scope": {"document_ids": ["selected"]}},
    }
    report = {
        "response": {
            "status": "answered",
            "searched_documents": ["other"],
            "citations": [],
            "document_answers": [
                {"document_id": "other", "claims": [{"text": "165", "citation_ids": ["invented"]}]}
            ],
        }
    }
    checks = check_answer(case, report, {})
    assert checks["expected_state"]
    assert not checks["canonical_citations"]
    assert not checks["selected_scope"]


def test_case_suite_has_prescoring_targets_and_complete_expectations():
    dataset = json.loads(Path("config/evaluation-cases.json").read_text())
    cases = dataset["cases"]
    assert 60 <= len(cases) <= 80
    assert len({c["id"] for c in cases}) == len(cases)
    assert dataset["acceptance_targets"]["human_critical_errors"] == 0
    assert all(
        {"category", "request", "expected_status", "required_phrases", "forbidden_phrases"}
        <= c.keys()
        for c in cases
    )
