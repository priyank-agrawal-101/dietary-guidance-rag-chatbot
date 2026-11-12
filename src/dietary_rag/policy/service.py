"""Fail-closed code policy, shared by request routing and post-generation checks."""

import re
import time

from dietary_rag.policy.classifier import LocalIntentClassifier
from dietary_rag.policy.contracts import IntentClassification, PolicyDecision
from dietary_rag.policy.rules import normalize, restricted_intents
from dietary_rag.settings import PolicySettings


class ClassificationUnavailable(RuntimeError):
    pass


class PolicyViolation(ValueError):
    pass


class PolicyService:
    def __init__(self, settings=None, classifier=None):
        self.settings = settings or PolicySettings()
        self.classifier = classifier or LocalIntentClassifier()

    def _route(self, intents, reason, context="intent"):
        for intent, template in (
            ("policy_bypass", "bypass"),
            ("medical_advice", "medical"),
            ("calorie_weight_target", "target"),
            ("unrelated", "unrelated"),
        ):
            if intent in intents:
                return PolicyDecision(
                    route="out_of_scope", intents=intents, reason=reason, template=template
                )
        if "individual_food_nutrients" in intents:
            return PolicyDecision(
                route="requires_structured_data",
                intents=intents,
                reason=reason,
                template="nutrients",
            )
        if "ambiguous" in intents:
            return PolicyDecision(
                route="needs_clarification", intents=intents, reason=reason, template=context
            )
        return PolicyDecision(route="allow", intents=intents, reason=reason)

    def decide(self, question):
        restrictions = restricted_intents(question)
        if restrictions:
            return self._route(restrictions, "deterministic_restriction")
        started = time.monotonic()
        try:
            payload = self.classifier.classify(question)
            if isinstance(payload, IntentClassification):
                payload = payload.model_dump(mode="python")
            classification = IntentClassification.model_validate(payload)
        except TimeoutError:
            raise
        except Exception as exc:
            raise ClassificationUnavailable("intent classification unavailable") from exc
        if time.monotonic() - started > self.settings.classifier_timeout_seconds:
            raise TimeoutError("intent classification deadline exceeded")
        # Positive restrictions remain prohibitions even at low confidence.
        decision = self._route(
            classification.intents, "typed_classification", classification.context
        )
        if decision.route != "allow" and decision.route != "needs_clarification":
            return decision
        if (
            classification.confidence < self.settings.minimum_confidence
            or classification.needs_context
        ):
            return self._route(("ambiguous",), "unresolved_intent", classification.context)
        return decision

    def check_claims(self, claims):
        """Phase 5 must call this on each claim before rendering; this is not grounding.

        Claims are declarative, so reusing a question-only allow classifier would
        overblock them. Every claim is classified and any uncertainty is withheld.
        """
        for claim in claims:
            if not isinstance(claim, str) or not claim.strip():
                raise PolicyViolation("empty or invalid claim")
            value = normalize(claim)
            if any(
                re.search(pattern, value)
                for pattern in (
                    r"\b\d+(?:\.\d+)?\s*(?:kcal|calories?)\b.{0,35}\b(?:daily|per day|each day|a day)\b",
                    r"\b(?:you|your|patient)\b.{0,50}\b(?:weigh|weight|calorie|kcal|bmi)\b",
                    r"\b(?:treats?|cures?|reverses?|lowers?|reduces?|controls?)\b.{0,50}\b(?:diabetes|disease|blood sugar|blood pressure|cholesterol|symptoms)\b",
                    r"\b(?:contains?|provides?|has|have)\b.{0,15}\b\d+(?:\.\d+)?\s*(?:g|mg|grams?|milligrams?|kcal|calories?)\b.{0,25}\b(?:protein|fat|carbs?|carbohydrates?|sodium|sugar|fiber|potassium|calcium|iron)\b",
                    r"\b(?:contains?|provides?|has|have)\b.{0,15}\b\d+(?:\.\d+)?\s*(?:kcal|calories?)\b",
                    r"\b\d+(?:\.\d+)?\s*(?:g|mg|grams?|milligrams?)\s+(?:of\s+)?(?:protein|fat|carbs?|carbohydrates?|sodium|sugar|fiber)\b.{0,35}\b(?:in|per)\b",
                )
            ):
                raise PolicyViolation(
                    "generated claim supplies prohibited advice or nutrient values"
                )
            decision = self.decide(claim)
            if decision.route == "requires_structured_data" and label_explanation(value):
                # A declarative explanation of label formats/comparison is not a
                # database lookup. Numeric nutrient/target guards above still apply.
                continue
            if decision.route != "allow":
                raise PolicyViolation("generated claim violates or has unresolved policy scope")


def label_explanation(value):
    """Narrow literacy exception for complete source prose, never question routing."""
    return bool(
        re.search(r"dual.column\s+labels?", value)
        and "per serving" in value
        and "per package" in value
        and "indicate the amounts of calories and nutrients" in value
        or re.search(r"nutrition facts labels?", value)
        and "compare sodium levels in foods" in value
        and "no-salt-added" in value
    )
