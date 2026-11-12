"""Frozen evidence-based strategy evaluation and quality-first selection."""

import json
import platform
import resource
import statistics
import time
from collections import Counter
from pathlib import Path

from dietary_rag.corpus.identity import sha256_bytes, stable_id
from dietary_rag.ingestion.chunk_evaluation import matches
from dietary_rag.retrieval.contracts import SearchRequest

REGRESSION_CATEGORIES = {
    "poultry-temperature": "temperatures",
    "whole-meat-rest": "temperatures",
    "ground-meat": "temperatures",
    "grilling-qualification": "temperatures",
    "hot-weather": "handling",
    "dining-sodium": "sodium",
    "produce-soap": "produce",
    "prewashed-produce": "produce",
}


def load_frozen_cases(regressions, heldout, freeze, manifest, chunks):
    reg_bytes, held_bytes = Path(regressions).read_bytes(), Path(heldout).read_bytes()
    receipt = json.loads(Path(freeze).read_text())
    if (
        receipt.get("version") != "retrieval_case_freeze_v1"
        or receipt["regressions_sha256"] != sha256_bytes(reg_bytes)
        or receipt["heldout_sha256"] != sha256_bytes(held_bytes)
        or receipt["chunk_snapshot_id"] != manifest["chunk_snapshot"]["snapshot_id"]
    ):
        raise ValueError("evaluation questions/evidence differ from frozen review receipt")
    regression_cases, heldout_cases = (
        json.loads(reg_bytes)["cases"],
        json.loads(held_bytes)["cases"],
    )
    if len(regression_cases) != 16 or len(heldout_cases) != 32:
        raise ValueError("evaluation requires the frozen 16 regressions and 32 held-out cases")
    categories = Counter(c["category"] for c in heldout_cases)
    if categories != Counter(
        {
            k: 4
            for k in (
                "temperatures",
                "storage",
                "handling",
                "pregnancy",
                "labels",
                "produce",
                "sodium",
                "adult_child",
            )
        }
    ):
        raise ValueError("held-out cases must cover all eight categories four times")
    cases, identifiers = [], set()
    for split, selected in (("regression", regression_cases), ("heldout", heldout_cases)):
        for case in selected:
            if (
                case["id"] in identifiers
                or not case["required_phrases"]
                or not case["document_ids"]
            ):
                raise ValueError("duplicate/empty evaluation case")
            identifiers.add(case["id"])
            expected = sorted(c.chunk_id for c in chunks.values() if matches(c, case))
            if (
                not expected
                or split == "heldout"
                and expected != sorted(receipt["expected_chunk_ids"].get(case["id"], ()))
            ):
                raise ValueError(f"frozen expected evidence missing/changed for {case['id']}")
            category = case.get(
                "category", REGRESSION_CATEGORIES.get(case["id"], case["id"].split("-", 1)[0])
            )
            cases.append(
                {
                    **case,
                    "split": split,
                    "category": category,
                    "expected_chunk_ids": expected,
                    "expected_evidence_types": sorted({chunks[i].chunk_type for i in expected}),
                }
            )
    return cases, receipt


def metrics(rows):
    return {
        "cases": len(rows),
        "complete_evidence_hits_at_5": sum(r["complete_evidence_hit"] for r in rows),
        "complete_evidence_hit_rate_at_5": sum(r["complete_evidence_hit"] for r in rows) / len(rows)
        if rows
        else 0.0,
        "mrr_at_5": sum(
            1 / r["first_complete_rank"] if r["first_complete_rank"] else 0 for r in rows
        )
        / len(rows)
        if rows
        else 0.0,
    }


def evaluate_retriever(retriever, cases, receipt, *, regressions_only=False):
    selected = [c for c in cases if not regressions_only or c["split"] == "regression"]

    # Test corpus-wide ranking separately from a question's explicit name syntax.
    # Expected document_ids remain labels; they never restrict this quality run.
    def full_corpus_question(case):
        question, _selection, issues = retriever.resolver.resolve(
            SearchRequest(question=case["question"]), retriever.documents
        )
        if issues:
            raise ValueError("frozen question contains an invalid document selection")
        return question

    first_started = time.perf_counter()
    retriever.search(SearchRequest(question=full_corpus_question(selected[0])))
    first_query_seconds = time.perf_counter() - first_started
    results, latencies, embedding_times = [], [], []
    for case in selected:
        start = time.perf_counter()
        result = retriever.search(SearchRequest(question=full_corpus_question(case), limit=5))
        latencies.append(time.perf_counter() - start)
        embedding_times.append(result.audit.query_embedding_seconds)
        rank = next((h.rank for h in result.hits if matches(h.chunk, case)), None)
        results.append(
            {
                "case_id": case["id"],
                "question": case["question"],
                "full_corpus_question": full_corpus_question(case),
                "split": case["split"],
                "category": case["category"],
                "expected_evidence_types": case["expected_evidence_types"],
                "expected_chunk_ids": case["expected_chunk_ids"],
                "complete_evidence_hit": rank is not None,
                "first_complete_rank": rank,
                "warm_query_seconds": latencies[-1],
                "top_chunks": [
                    {
                        "chunk_id": h.chunk.chunk_id,
                        "document_id": h.document.document_id,
                        "chunk_type": h.chunk.chunk_type,
                        "rank": h.rank,
                        "score": h.score,
                        "complete_evidence": matches(h.chunk, case),
                    }
                    for h in result.hits
                ],
                "audit": result.audit.model_dump(mode="json"),
            }
        )
    scope_checks = [
        {
            "case_id": row["case_id"],
            "check": "full_corpus_search_scope",
            "passed": row["audit"]["status"] == "searched"
            and set(row["audit"]["searched_document_ids"]) == set(retriever.documents),
        }
        for row in results
    ]
    for case in selected:
        request = SearchRequest(
            question=case["question"], selected_document_ids=tuple(case["document_ids"]), limit=5
        )
        result = retriever.search(request)
        scope_checks.append(
            {
                "case_id": case["id"],
                "check": "selected_document_isolation",
                "passed": result.audit.status == "searched"
                and set(result.audit.searched_document_ids) == set(case["document_ids"])
                and all(h.document.document_id in case["document_ids"] for h in result.hits),
            }
        )
    available = sorted(retriever.documents)
    edge_requests = {
        "unknown": SearchRequest(
            question="How should food be stored?", selected_document_ids=("unknown_document",)
        ),
        "mixed_unavailable": SearchRequest(
            question="How should food be stored?",
            selected_document_ids=(available[0], "unknown_document"),
        ),
        "known_unavailable": SearchRequest(
            question="What food should I choose?", document_names=("Healthy Eating Plate",)
        ),
        "ambiguous": SearchRequest(question="How should food be stored?", document_names=("CDC",)),
        "conflicting": SearchRequest(
            question="According to Minnesota storage chart, how long can eggs keep?",
            selected_document_ids=("cdc_food_safety",),
        ),
    }
    for name, request in edge_requests.items():
        result = retriever.search(request)
        scope_checks.append(
            {
                "check": name,
                "passed": result.audit.status == "invalid_selection"
                and not result.hits
                and not result.audit.searched_document_ids,
            }
        )
    official = retriever.search(
        SearchRequest(question="What are safe food handling steps?", official_only=True)
    )
    scope_checks.append(
        {
            "check": "official_only",
            "passed": official.audit.status == "searched"
            and all(
                retriever.documents[i].source_tier == "official_guidance"
                for i in official.audit.searched_document_ids
            )
            and all(h.document.source_tier == "official_guidance" for h in official.hits),
        }
    )
    no_secondary = retriever.search(
        SearchRequest(
            question="How should I store food?", source_tiers=("institutional_reference",)
        )
    )
    scope_checks.append(
        {
            "check": "absent_tier_no_fallback",
            "passed": no_secondary.audit.status == "no_eligible_documents"
            and not no_secondary.hits,
        }
    )
    comparison = retriever.search(
        SearchRequest(
            question="What do the sources say about safe cooking and storage?",
            comparison=True,
            limit=min(20, retriever.config.candidate_chunks),
        )
    )
    counts = Counter(h.document.document_id for h in comparison.hits)
    scope_checks.append(
        {
            "check": "comparison_budgets_and_audit",
            "passed": set(comparison.audit.searched_document_ids) == set(available)
            and len(comparison.hits) <= retriever.config.candidate_chunks
            and all(n <= retriever.config.per_document_candidates for n in counts.values()),
        }
    )
    groups = {
        split: metrics([r for r in results if r["split"] == split])
        for split in ("regression", "heldout")
    }
    provenance_ok = all(
        all(h["document_id"] in r["audit"]["searched_document_ids"] for h in r["top_chunks"])
        for r in results
    )
    passed = (
        groups["regression"]["complete_evidence_hits_at_5"] == 16
        and groups["heldout"]["complete_evidence_hits_at_5"] >= 29
        and all(c["passed"] for c in scope_checks)
        and provenance_ok
        and not regressions_only
    )
    memory = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "strategy": retriever.config.strategy,
        "corpus_snapshot_id": retriever.manifest["snapshot_id"],
        "retrieval_configuration": retriever.config.model_dump(mode="json"),
        "retrieval_configuration_id": retriever.config.configuration_id,
        "case_freeze": receipt,
        "regressions_only": regressions_only,
        "metrics": groups,
        "by_category": {
            category: metrics([r for r in results if r["category"] == category])
            for category in sorted({r["category"] for r in results})
        },
        "by_evidence_type": {
            kind: metrics([r for r in results if kind in r["expected_evidence_types"]])
            for kind in ("prose", "list", "table")
        },
        "scope_checks": scope_checks,
        "scope_leakage": not all(c["passed"] for c in scope_checks),
        "provenance_resolution_complete": provenance_ok,
        "passes_selection_gates": passed,
        "performance": {
            "validated_index_startup_seconds": retriever.startup_seconds,
            "cold_first_query_seconds": first_query_seconds,
            "warm_end_to_end_query_median_seconds": statistics.median(latencies),
            "warm_end_to_end_query_p95_seconds": sorted(latencies)[
                max(0, int(len(latencies) * 0.95) - 1)
            ],
            "warm_query_embedding_median_seconds": statistics.median(embedding_times),
            "peak_process_memory_bytes": memory if platform.system() == "Darwin" else memory * 1024,
            "paired_index_bytes": sum(
                p.stat().st_size for p in retriever.directory.rglob("*") if p.is_file()
            ),
            "by_category_median_seconds": {
                category: statistics.median(
                    r["warm_query_seconds"] for r in results if r["category"] == category
                )
                for category in sorted({r["category"] for r in results})
            },
            "by_evidence_type_median_seconds": {
                kind: statistics.median(
                    r["warm_query_seconds"] for r in results if kind in r["expected_evidence_types"]
                )
                for kind in sorted({k for r in results for k in r["expected_evidence_types"]})
            },
        },
        "machine": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "dependencies": retriever.processor.metadata["dependencies"],
        },
        "results": results,
        "limitation": "Small project-authored frozen evidence diagnostic; literal complete-evidence matching is not answer quality or independent population evaluation. Scope negatives for absent tiers are supplemented by adversarial tests.",
    }


def select_strategy(reports):
    from dietary_rag.retrieval.contracts import RetrievalConfig

    if {r["strategy"] for r in reports} != {"dense", "bm25", "hybrid"} or len(reports) != 3:
        raise ValueError("selection requires exactly three strategy reports")
    reference = reports[0]
    for report in reports:
        config = RetrievalConfig.model_validate(report["retrieval_configuration"])
        if (
            config.strategy != report["strategy"]
            or config.configuration_id != report["retrieval_configuration_id"]
        ):
            raise ValueError("strategy report configuration identity mismatch")
        a = {k: v for k, v in report["retrieval_configuration"].items() if k != "strategy"}
        b = {k: v for k, v in reference["retrieval_configuration"].items() if k != "strategy"}
        if (
            report["corpus_snapshot_id"] != reference["corpus_snapshot_id"]
            or report["case_freeze"] != reference["case_freeze"]
            or a != b
        ):
            raise ValueError("strategy comparison mixes snapshots, cases or retrieval budgets")
    passing = []
    for report in reports:
        for split, count in (("regression", 16), ("heldout", 32)):
            rows = [r for r in report["results"] if r["split"] == split]
            if len(rows) != count or metrics(rows) != report["metrics"][split]:
                raise ValueError("selection metrics differ from complete frozen case results")
        passes = (
            report["metrics"]["regression"]["complete_evidence_hits_at_5"] == 16
            and report["metrics"]["heldout"]["complete_evidence_hits_at_5"] >= 29
            and not report["scope_leakage"]
            and report["provenance_resolution_complete"]
            and bool(report["scope_checks"])
            and all(c["passed"] for c in report["scope_checks"])
            and not report["regressions_only"]
        )
        if passes != report["passes_selection_gates"]:
            raise ValueError("selection gate flag differs from reported checks")
        if passes:
            passing.append(report)
    preference = {"bm25": 0, "dense": 1, "hybrid": 2}
    passing.sort(
        key=lambda r: (
            -r["metrics"]["heldout"]["complete_evidence_hits_at_5"],
            -round(r["metrics"]["heldout"]["mrr_at_5"], 3),
            preference[r["strategy"]],
        )
    )
    selected = passing[0] if passing else None
    payload = {
        "corpus_snapshot_id": reference["corpus_snapshot_id"],
        "case_freeze": reference["case_freeze"],
        "strategy_reports": reports,
        "selected_strategy": selected["strategy"] if selected else None,
        "selected_configuration_id": selected["retrieval_configuration_id"] if selected else None,
        "selected_configuration": selected["retrieval_configuration"] if selected else None,
        "selection_rule": "Pass all gates, then held-out hit@5, MRR@5 rounded to 3 decimals, BM25/dense/hybrid tie preference",
        "active": False,
        "status": "passing_strategy_selected_no_activation"
        if selected
        else "no_passing_strategy_activation_blocked",
    }
    return {"selection_report_id": stable_id("retrieval_selection", payload), **payload}
