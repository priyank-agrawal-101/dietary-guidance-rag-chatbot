"""Canonical identity, complete contextual quotes, quantities, qualifiers and entailment."""

import re
from collections import Counter

from dietary_rag.answering.adapters import typed_payload
from dietary_rag.answering.contracts import AnswerValidationError, DraftAnswer, SemanticVerdict
from dietary_rag.answering.evidence import whitespace
from dietary_rag.corpus.identity import sha256_bytes
from dietary_rag.corpus.registry import LOCAL_TEXT_RIGHTS
from dietary_rag.policy.rules import normalize


def numbers(text):
    # No unit conversions: retain original values/ranges and their units.
    return re.findall(r"\d+(?:[.,]\d+)?", text)


def qualifiers(text):
    value = normalize(text)
    return {
        m[0]
        for m in re.finditer(
            r"\b(?:not|never|no|without|don't|don’t|raw|cooked|ground|whole|fresh|unopened|opened|pasteurized|unpasteurized|above|below|at least|at most|or less|or more|fewer|within|after|before|until|riskier|safer|refrigerator|freezer|taste loss|per serving)\b",
            value,
        )
    }


def units_signature(text):
    return re.findall(
        r"(?:\d+(?:\.\d+)?\s*(?:°\s*[fc]|%|hours?|minutes?|days?|months?|mg|grams?|g)\b)",
        normalize(text),
    )


def unit_labels(text):
    return Counter(
        re.findall(
            r"°\s*[fc]\b|%|\b(?:days?|months?|hours?|minutes?|mg|grams?|g)\b", normalize(text)
        )
    )


def validate_draft(draft, prepared, generation, verifier, policy, confidence):
    try:
        draft = typed_payload(draft, DraftAnswer)
    except (ValueError, TypeError) as exc:
        raise AnswerValidationError("draft_schema") from exc
    audit = prepared.search_result.audit
    if generation.corpus_snapshot_id != audit.corpus_snapshot_id:
        raise AnswerValidationError("snapshot_mismatch")
    pin = getattr(prepared.evidence_request, "pin", None)
    if pin is not None and (
        pin.release.corpus_snapshot_id != audit.corpus_snapshot_id
        or pin.release.release_id != audit.release_id
        or pin.generation != audit.activation_generation
    ):
        raise AnswerValidationError("snapshot_mismatch")
    if draft.unanswered_aspects != generation.unanswered_aspects:
        raise AnswerValidationError("unsupported_gap")
    hits = {h.chunk.chunk_id: h for h in prepared.search_result.hits}
    validated = []
    covered_units = set()
    for group in draft.document_answers:
        if (
            group.document_id not in generation.evidence_groups
            or group.document_id not in audit.searched_document_ids
        ):
            raise AnswerValidationError("document_scope")
        available = generation.evidence_groups[group.document_id]
        for claim in group.claims:
            for support in claim.evidence:
                hit = hits.get(support.chunk_id)
                if hit is None:
                    raise AnswerValidationError("unretrieved_chunk")
                if (
                    hit.document.document_id != group.document_id
                    or hit.chunk.document_id != group.document_id
                ):
                    raise AnswerValidationError("cross_document_support")
                try:
                    resolved = prepared.evidence_request.resolve_evidence(support.chunk_id)
                except (KeyError, ValueError) as exc:
                    raise AnswerValidationError("stale_evidence") from exc
                chunk, doc = resolved["chunk"], resolved["build"].document
                if (
                    chunk != hit.chunk
                    or doc != hit.document
                    or chunk.document_version_id != doc.document_version_id
                    or sha256_bytes(chunk.text.encode()) != chunk.text_sha256
                ):
                    raise AnswerValidationError("canonical_identity")
                if (
                    doc.content_role != "consumer_guidance"
                    or doc.target_audience != "US"
                    or not (doc.jurisdiction == "US" or doc.jurisdiction.startswith("US-"))
                    or doc.rights_review.status not in LOCAL_TEXT_RIGHTS
                    or not doc.eligibility.default_index
                ):
                    raise AnswerValidationError("applicability")
                if whitespace(support.support_quote) not in whitespace(chunk.text):
                    raise AnswerValidationError("quote_mismatch")
            pairs = [(s.chunk_id, whitespace(s.support_quote)) for s in claim.evidence]
            if len(set(pairs)) != len(pairs):
                raise AnswerValidationError("duplicate_support")
            matched = []
            used = set()
            for unit in available:
                required = {(s.chunk_id, whitespace(s.support_quote)) for s in unit.evidence}
                if required <= set(pairs):
                    matched.append(unit)
                    used.update(required)
            if not matched or used != set(pairs):
                raise AnswerValidationError("incomplete_context")
            if len(matched) != 1:
                raise AnswerValidationError("non_atomic_claim")
            # Same source unit must not be restated as multiple independent claims.
            if any(u.unit_id in covered_units for u in matched):
                raise AnswerValidationError("duplicate_claim")
            evidence_text = whitespace(" ".join(u.claim_text for u in matched))
            if (
                numbers(claim.claim_text) != numbers(evidence_text)
                or units_signature(claim.claim_text) != units_signature(evidence_text)
                or unit_labels(claim.claim_text) != unit_labels(evidence_text)
            ):
                raise AnswerValidationError("numeric_or_unit_mismatch")
            if not qualifiers(evidence_text) <= qualifiers(claim.claim_text):
                raise AnswerValidationError("omitted_conditions")
            verdict = typed_payload(verifier.verify(claim.claim_text, matched), SemanticVerdict)
            if (
                not verdict.supported
                or verdict.reason != "entailed"
                or verdict.confidence < confidence
            ):
                raise AnswerValidationError("semantic_support")
            try:
                policy.check_claims([claim.claim_text])
            except ValueError as exc:
                raise AnswerValidationError("post_generation_policy") from exc
            covered_units.update(u.unit_id for u in matched)
            validated.append((group.document_id, claim, tuple(matched)))
    # Adequate assessed evidence cannot be silently omitted by a generator.
    expected = {u.unit_id for units in generation.evidence_groups.values() for u in units}
    if covered_units != expected:
        raise AnswerValidationError("omitted_supported_aspect")
    return draft, validated
