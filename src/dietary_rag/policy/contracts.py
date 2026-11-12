"""Classifier output is untrusted data; routing belongs to application code."""

from typing import Annotated, Literal, Protocol

from pydantic import Field, StrictBool, model_validator

from dietary_rag.corpus.contracts import Contract, NonEmpty

Intent = Literal[
    "allowed_guidance",
    "medical_advice",
    "calorie_weight_target",
    "individual_food_nutrients",
    "ambiguous",
    "policy_bypass",
    "unrelated",
]


class IntentClassification(Contract):
    intents: tuple[Intent, ...]
    confidence: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
    needs_context: StrictBool = False
    context: Literal["intent", "food_storage"] = "intent"

    @model_validator(mode="after")
    def unique_intents(self):
        if not self.intents or len(set(self.intents)) != len(self.intents):
            raise ValueError("classification requires nonempty unique intents")
        return self


class IntentClassifier(Protocol):
    def classify(self, question: str) -> IntentClassification: ...


class PolicyDecision(Contract):
    route: Literal["allow", "out_of_scope", "requires_structured_data", "needs_clarification"]
    intents: tuple[Intent, ...]
    reason: NonEmpty
    template: (
        Literal["medical", "target", "bypass", "unrelated", "nutrients", "intent", "food_storage"]
        | None
    ) = None
