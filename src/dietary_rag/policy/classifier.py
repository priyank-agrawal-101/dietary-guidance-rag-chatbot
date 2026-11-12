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
        if re.search(
            r"\b(?:presidents?|prime minister|politics|elections?|stock market|invest\w*|football|programming)\b",
            value,
        ):
            return IntentClassification(intents=("unrelated",), confidence=0.95)
        if re.search(r"\b(?:lab results|blood tests|my condition)\b", value):
            return IntentClassification(intents=("ambiguous",), confidence=0.5)
        clauses = re.split(
            r"[;]|\band\s+(?=(?:how|what|can|should|help|give|tell|which|calculate|set|recommend|does|is)\b)",
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
                and not contextual_guidance(clause, value)
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
            r"\b(?:daily values?|serving sizes?|special requests|restaurant|internal temperature)\b|%\s*dv",
            value,
        ):
            return IntentClassification(intents=("allowed_guidance",), confidence=0.95)
        if general_guidance(value):
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
                r"\b(?:safe|safer|safety|stor(?:e|age|ing)|refrigerat\w*|freez\w*|frozen|thaw\w*|cook\w*|leftovers?|food groups?|balanced|healthy eating|dietary guidelines|food selection|nutrition (?:facts )?labels?|cross.contamination|wash\w*|handling|grill\w*|thermometer|best.before|use.by)\b",
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


def general_guidance(value):
    """Recognized consumer topics, not permission based on a generic food noun."""
    return any(
        re.search(pattern, value)
        for pattern in (
            r"\b(?:microwav\w*|defrost\w*|refreez\w*|picnic|perishable|power (?:cut|outage)|electricity returns)\b",
            r"\b(?:sodium|salt|salty|salt-free)\b.{0,80}\b(?:remove|rinse|cook|season|flavor|prepare|foods?|vegetables?|menu|tips?|instead|substitut\w*)\b",
            r"\b(?:herbs?|citrus|seasonings?|flavor\w*)\b.{0,80}\b(?:salty|salt|food|prepare|home)\b",
            r"\b(?:dairy|hydration|hydrated|caffeinat\w*)\b",
            r"\b(?:cooking|edible|olive|vegetable|canola|sunflower|coconut|mustard) oils?\b",
            r"\b(?:remove|reduce|limit|rinse\w*)\b.{0,80}\bsodium\b",
            r"\bdual.column labels?\b",
            r"\bboil\w*\b.{0,80}\b(?:juice|cider)\b",
            r"\b(?:juice|cider)\b.{0,80}\b(?:pasteurized|unpasteurized|safer|boil\w*)\b",
            r"\b(?:storage|salads?|foods?)\b.{0,80}\bchill\w*\b",
            r"\bcheck\b.{0,60}\b(?:added sugars?|sodium)\b",
            r"\b(?:produce|watermelons?|melons?|salad greens|lettuce|mushrooms|cucumbers)\b.{0,80}\b(?:buy\w*|shop\w*|chill\w*|pre-cut|packaged|cold|scrub\w*|dry|refrigerat\w*|season)\b",
            r"\b(?:buy\w*|shop\w*|scrub\w*|cold|affordable)\b.{0,80}\b(?:produce|watermelons?|melons?|salad greens|lettuce|mushrooms|cucumbers)\b",
            r"\b(?:sugar|syrup|honey)\b.{0,35}\blabels?\b|\blabels?\b.{0,35}\b(?:added sugar|per-serving|per-package|dual.column)\b",
        )
    )


def contextual_guidance(clause, question):
    """Resolve a narrow follow-up action using a food topic in the same question.

    Unrecognized second requests still clarify; the parent topic cannot permit an
    arbitrary clause. Deterministic restrictions have already checked the whole input.
    """
    return bool(
        re.search(r"\bthermometer\b", question)
        and re.search(r"\bclean (?:it|them)\b", clause)
        or re.search(r"\b(?:sodium|vegetables?|produce)\b", question)
        and re.search(r"\bseason (?:it|them)\b", clause)
        or re.search(r"\bhydration\b", question)
        and re.search(r"\bwater\b.{0,80}\bcounts?\b", clause)
    )
