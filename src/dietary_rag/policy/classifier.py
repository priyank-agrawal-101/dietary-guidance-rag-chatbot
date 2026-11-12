"""Offline conservative classifier and validated adapter for optional intent providers."""

import re

from dietary_rag.policy.contracts import IntentClassification
from dietary_rag.policy.rules import normalize, restricted_intents


class TypedClassifierAdapter:
    """Provider receives only the question and must return the exact typed payload.

    Provider implementations must enforce their I/O timeout. No raw provider prose
    becomes a response; malformed results and exceptions are handled by PolicyService.
    """

    def __init__(self, classify):
        self.provider = classify

    def classify(self, question):
        return IntentClassification.model_validate(self.provider(question))


class LocalIntentClassifier:
    """Allow recognized general guidance; clarify unfamiliar or context-dependent intent."""

    def classify(self, question):
        value = normalize(question)
        restricted = restricted_intents(value)
        if restricted:
            return IntentClassification(intents=restricted, confidence=1.0)
        clauses = re.split(
            r"[;]|\band\s+(?=(?:how|what|can|should|help|give|tell|which|calculate|set|recommend)\b)",
            value,
        )
        if len(clauses) > 1:
            decisions = [self.classify(clause.strip()) for clause in clauses if clause.strip()]
            if any(
                d.intents != ("allowed_guidance",)
                and not (
                    re.search(r"\b(?:cook|cooked|cooking)\b", value)
                    and re.search(r"\brest\b", clause)
                )
                for clause, d in zip(clauses, decisions, strict=True)
            ):
                return IntentClassification(intents=("ambiguous",), confidence=0.5)
        if re.search(
            r"\b(?:is (?:it|this|that) safe|can i eat (?:it|this|that)|how long (?:can|does) (?:it|this|that))\b",
            value,
        ):
            return IntentClassification(
                intents=("ambiguous",), confidence=1.0, needs_context=True, context="food_storage"
            )
        if re.search(
            r"\b(?:daily value|serving size|special requests|restaurant|internal temperature)\b|%\s*dv",
            value,
        ):
            return IntentClassification(intents=("allowed_guidance",), confidence=0.95)
        if re.search(r"\d+\s*°\s*f\b", value) and re.search(
            r"\b(?:poultry|chicken|turkey|beef|pork|lamb|fish|leftovers|food)\b", value
        ):
            return IntentClassification(intents=("allowed_guidance",), confidence=0.95)
        if re.search(r"\b(?:heat|heated|heating)\b", value) and re.search(
            r"\b(?:cheese|deli|food)\b", value
        ):
            return IntentClassification(intents=("allowed_guidance",), confidence=0.95)
        if re.search(r"\bseparat\w*\b.{0,70}\b(?:raw meat|poultry|seafood|foods?)\b", value):
            return IntentClassification(intents=("allowed_guidance",), confidence=0.95)
        if (
            re.search(
                r"\b(?:safe|safer|safety|stor(?:e|age|ing)|refrigerat\w*|freez\w*|frozen|thaw\w*|cook\w*|leftovers?|food groups?|balanced|healthy eating|dietary guidelines|food selection|nutrition (?:facts )?label|cross.contamination|wash\w*|handling|grill\w*|thermometer|best.before|use.by)\b",
                value,
            )
            or re.search(
                r"\b(?:choose|select|eat|eating|servings?|portions?)\b.{0,50}\b(?:foods?|vegetables?|fruit|grains?|protein|diet|milk|cheese|melon)\b",
                value,
            )
            or re.search(r"\b(?:milk|cheese|melon)\b.{0,50}\bchoose\b", value)
        ):
            return IntentClassification(intents=("allowed_guidance",), confidence=0.95)
        return IntentClassification(intents=("ambiguous",), confidence=0.5)
