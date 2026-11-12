"""One bounded Groq call selects evidence IDs; code restores exact grounded claims."""

import json
import math
import time

from pydantic import Field

from dietary_rag.answering.adapters import INSTRUCTIONS
from dietary_rag.answering.contracts import (
    AnswerModelUnavailable,
    AnswerValidationError,
    DraftAnswer,
    DraftClaim,
    DraftDocument,
)
from dietary_rag.api.context import remaining_seconds
from dietary_rag.corpus.contracts import Contract
from dietary_rag.providers.usage import TokenBudgetExceeded, UsageLimitExceeded

ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
MAX_RESPONSE_BYTES = 65536


class UnitSelection(Contract):
    unit_ids: list[str] = Field(min_length=1, max_length=400)


def compact_input(request):
    units = [unit for group in request.evidence_groups.values() for unit in group]
    # Short IDs replace 64-character hashes; text occurs once, no duplicate support quotes.
    mapping = {f"u{index}": unit for index, unit in enumerate(units)}
    messages = [
        {
            "role": "system",
            "content": INSTRUCTIONS + "\nReturn unit_ids only. Include EVERY supplied unit ID "
            "exactly once, ordered within each document. Do not create text or new IDs.",
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "question": request.question,
                    "evidence": [
                        {"id": key, "document": unit.document_id, "text": unit.claim_text}
                        for key, unit in mapping.items()
                    ],
                    "repair_codes": request.repair_codes,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
    ]
    schema = {
        "type": "object",
        "properties": {"unit_ids": {"type": "array", "items": {"type": "string"}}},
        "required": ["unit_ids"],
        "additionalProperties": False,
    }
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "evidence_selection", "strict": True, "schema": schema},
    }
    return mapping, messages, response_format


class GroqGenerator:
    name = "groq_evidence_selection_v1"

    def __init__(
        self, model, settings, ledger, *, api_key=None, timeout_seconds=30, transport=None
    ):
        self.model, self.settings, self.ledger = model, settings, ledger
        self._api_key = api_key
        self.timeout_seconds, self.transport = timeout_seconds, transport

    @property
    def configured(self):
        return bool(self._api_key and self._api_key.strip())

    def generate(self, request):
        if not self.configured:
            raise AnswerModelUnavailable("generation credentials unavailable")
        mapping, messages, response_format = compact_input(request)
        # Conservative UTF-8 byte bound, including schema and a chat-template allowance.
        # This is deliberately NOT the historical E5 retrieval tokenizer.
        input_bound = (
            len(json.dumps([messages, response_format], ensure_ascii=False).encode("utf-8")) + 256
        )
        if input_bound > self.settings.max_input_tokens:
            raise TokenBudgetExceeded("complete evidence exceeds input token budget")
        try:
            import anyio
            import httpx
        except ImportError as exc:
            raise AnswerModelUnavailable("generation transport unavailable") from exc
        remaining_seconds(self.timeout_seconds)
        reservation = self.ledger.reserve(input_bound + self.settings.max_completion_tokens)
        actual_tokens = None
        try:
            stage_deadline = time.monotonic() + remaining_seconds(self.timeout_seconds)
            body = {
                "model": self.model,
                "messages": messages,
                "response_format": response_format,
                "temperature": 0,
                "reasoning_effort": "low",
                "include_reasoning": False,
                "max_completion_tokens": self.settings.max_completion_tokens,
                "stream": False,
            }
            # The async transport has a total deadline as well as socket timeouts.
            data = anyio.run(self._complete, body, stage_deadline)
            remaining_seconds(stage_deadline - time.monotonic())
            try:
                result = json.loads(data)
                usage = result.get("usage", {})
                total = usage.get("total_tokens")
                if type(total) is int and total >= 0:
                    actual_tokens = total
                choice = result["choices"][0]
                if choice["finish_reason"] != "stop":
                    raise AnswerValidationError("provider_incomplete")
                selection = UnitSelection.model_validate_json(choice["message"]["content"])
                if len(set(selection.unit_ids)) != len(selection.unit_ids) or set(
                    selection.unit_ids
                ) != set(mapping):
                    raise AnswerValidationError("provider_unit_selection")
            except AnswerValidationError:
                raise
            except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
                raise AnswerValidationError("provider_schema") from exc
        except (TimeoutError, AnswerModelUnavailable, AnswerValidationError):
            raise
        except Exception as exc:
            if isinstance(exc, httpx.TimeoutException):
                raise TimeoutError("generation deadline exceeded") from exc
            raise AnswerModelUnavailable("generation dependency unavailable") from exc
        finally:
            self.ledger.settle(reservation, actual_tokens)
        groups = {}
        for identifier in selection.unit_ids:
            unit = mapping[identifier]
            groups.setdefault(unit.document_id, []).append(
                DraftClaim(claim_text=unit.claim_text, evidence=unit.evidence)
            )
        return DraftAnswer(
            document_answers=tuple(
                DraftDocument(document_id=doc, claims=tuple(claims))
                for doc, claims in groups.items()
            ),
            unanswered_aspects=request.unanswered_aspects,
        )

    async def _complete(self, body, deadline):
        import anyio
        import httpx

        # No automatic retry, redirect, tools, or environment proxy credentials.
        timeout = remaining_seconds(deadline - time.monotonic())
        with anyio.fail_after(timeout):
            async with httpx.AsyncClient(
                transport=self.transport, trust_env=False, follow_redirects=False
            ) as client:
                async with client.stream(
                    "POST",
                    ENDPOINT,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=body,
                    timeout=timeout,
                ) as response:
                    if response.status_code == 429:
                        try:
                            delay = float(response.headers.get("retry-after", "60"))
                            if not math.isfinite(delay):
                                delay = 60
                        except ValueError:
                            delay = 60
                        delay = max(1, min(86400, math.ceil(delay)))
                        self.ledger.cooldown(delay)
                        raise UsageLimitExceeded(delay)
                    if response.status_code != 200:
                        raise AnswerModelUnavailable("generation dependency unavailable")
                    data = bytearray()
                    async for part in response.aiter_bytes():
                        data.extend(part)
                        if len(data) > MAX_RESPONSE_BYTES:
                            raise AnswerModelUnavailable("generation response exceeds limit")
                    return data
