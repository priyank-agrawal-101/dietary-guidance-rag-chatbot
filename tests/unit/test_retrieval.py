"""Scope resolution, independent rank fusion and strict strategy selection."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from dietary_rag.retrieval.contracts import RetrievalConfig, SearchRequest
from dietary_rag.retrieval.engine import reciprocal_rank_fusion
from dietary_rag.retrieval.evaluation import metrics, select_strategy
from dietary_rag.retrieval.lexical import BM25, terms
from dietary_rag.retrieval.resolution import DocumentResolver

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def resolver(reviewed_registry):
    return DocumentResolver(
        reviewed_registry, json.loads((ROOT / "config/document-aliases.json").read_text())
    )


def test_bm25_scope_statistics_and_no_prefix_or_zero_score_fallback():
    lexical = BM25({"a": "fish 145 F", "b": "fish fish 165 F"})
    assert lexical.rank("145°F")[0][0] == "a"
    assert lexical.rank("zzzz") == []
    assert lexical.rank("passage") == []
    assert len(lexical.rank("zzzz", include_zero=True)) == 2
    scoped = BM25({"a": "fish 145 F"})
    assert scoped.rank("fish")[0][1] != dict(lexical.rank("fish"))["a"]
    assert terms("CHILD’S 165°F") == ["child", "s", "165", "f"]
    assert BM25({}).rank("food") == []


def test_rrf_uses_ranks_not_incomparable_raw_scores():
    branches = {"dense": [("a", 0.999), ("b", 0.998)], "bm25": [("b", 1000), ("c", 900)]}
    fused = reciprocal_rank_fusion(branches, 60)
    assert fused[0] == ("b", pytest.approx(1 / 61 + 1 / 62))
    assert fused[1:] == [("a", 1 / 61), ("c", 1 / 62)]
    branches["bm25"] = [("b", 0.00001), ("c", 0.000001)]
    assert reciprocal_rank_fusion(branches, 60) == fused


@pytest.mark.parametrize(
    "selection,kind",
    [("CDC", "ambiguous"), ("missing source", "unknown"), ("Healthy Eating Plate", "unavailable")],
)
def test_named_failures_never_expand_scope(resolver, selection, kind):
    question, ids, issues = resolver.resolve(
        SearchRequest(question="What should I eat?", document_names=(selection,)),
        {"mn_storage_chart", "cdc_food_safety"},
    )
    assert question == "What should I eat?"
    assert issues[0].kind == kind


def test_names_aliases_prefix_and_conflicts(resolver):
    request = SearchRequest(
        question="According to the Minnesota storage chart, how long can eggs keep?"
    )
    question, ids, issues = resolver.resolve(request, {"mn_storage_chart"})
    assert (question, ids, issues) == ("how long can eggs keep?", ("mn_storage_chart",), ())
    conflicted = request.model_copy(update={"selected_document_ids": ("cdc_food_safety",)})
    assert (
        resolver.resolve(conflicted, {"mn_storage_chart", "cdc_food_safety"})[2][0].kind
        == "conflicting"
    )
    malformed = SearchRequest(question="According to CDC how long can eggs keep?")
    assert resolver.resolve(malformed, {"cdc_food_safety"})[2][0].kind == "invalid_name_syntax"
    unknown = SearchRequest(question="food", selected_document_ids=("unknown",))
    assert resolver.resolve(unknown, {"mn_storage_chart"})[2][0].kind == "unknown"


def test_alias_collisions_stay_ambiguous_and_unknown_alias_ids_rejected(reviewed_registry):
    aliases = {
        "version": "document_aliases_v1",
        "aliases": {"Chart": ["mn_storage_chart", "mn_safe_temperatures"]},
    }
    resolver = DocumentResolver(reviewed_registry, aliases)
    assert (
        resolver.resolve(
            SearchRequest(question="food", document_names=("Chart",)),
            {"mn_storage_chart", "mn_safe_temperatures"},
        )[2][0].kind
        == "ambiguous"
    )
    aliases["aliases"]["Chart"] = ["unknown"]
    with pytest.raises(ValueError):
        DocumentResolver(reviewed_registry, aliases)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"selected_document_ids": ()},
        {"document_names": ()},
        {"source_tiers": ()},
        {"official_only": True, "source_tiers": ("institutional_reference",)},
        {"selected_document_ids": ("a", "a")},
        {"limit": 0},
    ],
)
def test_request_rejects_invalid_bounds_and_empty_scopes(kwargs):
    with pytest.raises(ValidationError):
        SearchRequest(question="food", **kwargs)


def comparison_reports():
    reports = []
    for strategy in ("dense", "bm25", "hybrid"):
        config = RetrievalConfig(strategy=strategy, resolution_configuration_id="resolver_test")
        rows = [
            {"split": split, "complete_evidence_hit": True, "first_complete_rank": 1}
            for split, n in (("regression", 16), ("heldout", 32))
            for _ in range(n)
        ]
        reports.append(
            {
                "strategy": strategy,
                "corpus_snapshot_id": "snapshot_test",
                "retrieval_configuration": config.model_dump(),
                "retrieval_configuration_id": config.configuration_id,
                "case_freeze": {"receipt": "same"},
                "results": rows,
                "metrics": {
                    s: metrics([r for r in rows if r["split"] == s])
                    for s in ("regression", "heldout")
                },
                "passes_selection_gates": True,
                "scope_leakage": False,
                "provenance_resolution_complete": True,
                "scope_checks": [{"passed": True}],
                "regressions_only": False,
            }
        )
    return reports


def test_selection_quality_then_rounded_mrr_then_simplicity():
    reports = comparison_reports()
    selected = select_strategy(reports)
    assert selected["selected_strategy"] == "bm25"
    assert selected["active"] is False
    for report in reports[:2]:
        row = report["results"][-1]
        row.update(complete_evidence_hit=False, first_complete_rank=None)
        report["metrics"]["heldout"] = metrics(report["results"][16:])
    assert select_strategy(reports)["selected_strategy"] == "hybrid"
    reports = comparison_reports()
    reports[1]["results"][-1]["first_complete_rank"] = 2
    reports[1]["metrics"]["heldout"] = metrics(reports[1]["results"][16:])
    assert select_strategy(reports)["selected_strategy"] == "dense"


def test_no_passing_strategy_blocks_activation():
    reports = comparison_reports()
    for report in reports:
        report.update(passes_selection_gates=False, scope_leakage=True)
        report["scope_checks"] = [{"passed": False}]
    selected = select_strategy(reports)
    assert selected["selected_strategy"] is None
    assert selected["status"] == "no_passing_strategy_activation_blocked"


@pytest.mark.parametrize(
    "mutation", ["configuration", "snapshot", "freeze", "metrics", "gate", "missing_cases"]
)
def test_selection_rejects_mixed_or_misreported_results(mutation):
    reports = copy.deepcopy(comparison_reports())
    if mutation == "configuration":
        reports[0]["retrieval_configuration"]["branch_candidates"] = 30
    elif mutation == "snapshot":
        reports[0]["corpus_snapshot_id"] = "other_snapshot"
    elif mutation == "freeze":
        reports[0]["case_freeze"] = {"receipt": "other"}
    elif mutation == "metrics":
        reports[0]["metrics"]["heldout"]["complete_evidence_hits_at_5"] = 31
    elif mutation == "gate":
        reports[0]["passes_selection_gates"] = False
    else:
        reports[0]["results"].pop()
    with pytest.raises(ValueError):
        select_strategy(reports)
