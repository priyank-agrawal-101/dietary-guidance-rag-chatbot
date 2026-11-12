"""Recheck frozen selection claims against compatible canonical evidence before publication."""

import math

from dietary_rag.corpus.chunk_repository import ChunkRepository
from dietary_rag.corpus.index_snapshot import validate_candidate
from dietary_rag.ingestion.chunk_evaluation import matches
from dietary_rag.retrieval.contracts import SearchRequest
from dietary_rag.retrieval.evaluation import load_frozen_cases, select_strategy


def validate_activation(
    directory, config, selection, case_directory, registry, processor, tokenizer, resolver
):
    if config.resolution_configuration_id != resolver.configuration_id:
        raise ValueError("activation resolver/registry configuration mismatch")
    reconstructed = select_strategy(selection["strategy_reports"])
    if reconstructed != selection:
        raise ValueError("selection report identity, winner or contents changed")
    if (
        selection["selected_strategy"] is None
        or selection["selected_configuration_id"] != config.configuration_id
        or selection["selected_configuration"] != config.model_dump(mode="json")
    ):
        raise ValueError("activation requires the selected passing retrieval configuration")
    validation = validate_candidate(directory, processor, tokenizer, registry)
    if validation["snapshot_id"] != selection["corpus_snapshot_id"]:
        raise ValueError("selection evidence belongs to a different corpus snapshot")
    if validation["pdf_documents"] < 5 or validation["documents"] <= validation["pdf_documents"]:
        raise ValueError(
            "Phase 3 activation requires reviewed HTML and at least five official PDFs"
        )
    import json

    manifest = json.loads((directory / "manifest.json").read_bytes())
    with ChunkRepository(directory / "evidence.sqlite", read_only=True) as repository:
        builds = repository.snapshot_builds(manifest["chunk_snapshot"]["snapshot_id"])
        if (
            sum(
                b.document.format == "pdf" and b.document.source_tier == "official_guidance"
                for b in builds
            )
            < 5
        ):
            raise ValueError(
                "Phase 3 activation requires at least five approved official PDF assets"
            )
        chunks = {c.chunk_id: c for b in builds for c in b.chunks}
        documents = {b.document.document_id for b in builds}
        cases, receipt = load_frozen_cases(
            case_directory / "regressions.json",
            case_directory / "heldout.json",
            case_directory / "freeze.json",
            manifest,
            chunks,
        )
        if selection["case_freeze"] != receipt:
            raise ValueError("selection receipt differs from retained frozen cases")
        expected_cases = {c["id"]: c for c in cases}
        expected_checks = {
            (kind, c["id"])
            for c in cases
            for kind in ("full_corpus_search_scope", "selected_document_isolation")
        } | {
            (kind, None)
            for kind in (
                "unknown",
                "mixed_unavailable",
                "known_unavailable",
                "ambiguous",
                "conflicting",
                "official_only",
                "absent_tier_no_fallback",
                "comparison_budgets_and_audit",
            )
        }
        machines = []
        for report in selection["strategy_reports"]:
            machines.append(report["machine"])
            if report["machine"]["dependencies"] != processor.metadata["dependencies"]:
                raise ValueError(
                    "selection dependency configuration differs from compatible snapshot"
                )
            checks = report["scope_checks"]
            if (
                len(checks) != len(expected_checks)
                or {(c["check"], c.get("case_id")) for c in checks} != expected_checks
                or any(type(c["passed"]) is not bool for c in checks)
            ):
                raise ValueError("selection has incomplete or invalid scope checks")
            rows = report["results"]
            if len(rows) != len(expected_cases) or {r["case_id"] for r in rows} != set(
                expected_cases
            ):
                raise ValueError("selection results do not cover exactly the frozen questions")
            for row in rows:
                case = expected_cases[row["case_id"]]
                for key in (
                    "question",
                    "split",
                    "category",
                    "expected_chunk_ids",
                    "expected_evidence_types",
                ):
                    if row[key] != case[key]:
                        raise ValueError("selection question or expected evidence changed")
                question, _selected, issues = resolver.resolve(
                    SearchRequest(question=case["question"]), documents
                )
                audit = row["audit"]
                if (
                    issues
                    or row["full_corpus_question"] != question
                    or audit["resolved_question"] != question
                    or audit["selected_document_ids"] is not None
                    or audit["status"] != "searched"
                    or set(audit["searched_document_ids"]) != documents
                    or audit["corpus_snapshot_id"] != validation["snapshot_id"]
                    or audit["retrieval_configuration_id"] != report["retrieval_configuration_id"]
                    or audit["strategy"] != report["strategy"]
                ):
                    raise ValueError(
                        "selection audit uses different question, scope or configuration"
                    )
                hits = row["top_chunks"]
                if len(hits) > 5 or len({h["chunk_id"] for h in hits}) != len(hits):
                    raise ValueError("selection hit@5 results exceed bounds or duplicate evidence")
                first_complete = None
                for rank, hit in enumerate(hits, 1):
                    chunk = chunks.get(hit["chunk_id"])
                    if (
                        chunk is None
                        or hit["rank"] != rank
                        or hit["document_id"] != chunk.document_id
                        or hit["chunk_type"] != chunk.chunk_type
                        or not math.isfinite(hit["score"])
                        or type(hit["complete_evidence"]) is not bool
                        or hit["complete_evidence"] != matches(chunk, case)
                    ):
                        raise ValueError(
                            "selection hit differs from canonical evidence or complete facts"
                        )
                    resolved = repository.resolve_chunk(chunk.chunk_id)
                    if resolved["chunk"] != chunk:
                        raise ValueError("selection provenance does not resolve")
                    if matches(chunk, case) and first_complete is None:
                        first_complete = rank
                if (
                    row["first_complete_rank"] != first_complete
                    or type(row["complete_evidence_hit"]) is not bool
                    or row["complete_evidence_hit"] != (first_complete is not None)
                ):
                    raise ValueError(
                        "selection metrics claim incomplete or missing evidence as a hit"
                    )
        if any(machine != machines[0] for machine in machines[1:]):
            raise ValueError("strategy performance comparison used different machines")
    return validation
