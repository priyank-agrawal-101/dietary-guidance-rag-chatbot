"""Conservative condition-aware assessment over complete retrieved evidence units.

No vector-score threshold declares support. Unknown topics and unmatched conditions
remain gaps. Whole prose contexts and complete table rows/headers/notes are retained.
"""

import re

from dietary_rag.answering.contracts import (
    Aspect,
    Coverage,
    EvidenceAssessment,
    EvidenceUnit,
    Support,
)
from dietary_rag.corpus.identity import stable_id
from dietary_rag.policy.rules import normalize


def whitespace(text):
    """Quote comparison collapses Unicode whitespace only; never case or punctuation."""
    return " ".join(text.split())


FOODS = {
    "chicken": r"\b(?:chicken|poultry)\b",
    "turkey": r"\b(?:turkey|poultry)\b",
    "beef": r"\b(?:beef|hamburger)\b",
    "pork": r"\bpork\b",
    "lamb": r"\blamb\b",
    "eggs": r"\beggs?\b",
    "fish": r"\b(?:fish|salmon)\b",
    "shellfish": r"\bshellfish\b",
    "milk": r"\bmilk\b",
    "cheese": r"\bcheese\w*\b",
    "melon": r"\bmelon\b",
    "leftovers": r"\bleftovers?\b",
    "tv dinners": r"\btv dinners?\b",
    "produce": r"\b(?:produce|fruits?|vegetables?)\b",
}


def units_for_hit(hit):
    chunk = hit.chunk
    if chunk.chunk_type == "table":
        parts = chunk.text.split("\n\n")
        prefix = parts[0].split("\n", 1)[0]
        notes = [p for p in parts if p.startswith("Note:")]
        rows = []
        for part in parts:
            if part.startswith("Note:"):
                continue
            if part.startswith("Table:"):
                part = part.partition("\n")[2]
            if part.strip():
                rows.append((prefix, part, *notes))
    else:
        paragraphs = chunk.text.split("\n\n")
        # Retained HTML temperature charts use complete category/value pairs.
        pairs = [
            paragraphs[i - 1] + "\n\n" + p
            for i, p in enumerate(paragraphs)
            if i > 0
            and re.match(r"^\d+\s*°\s*F\b", p)
            and not re.search(r"\d+\s*°\s*F\b", paragraphs[i - 1])
        ]
        # Other prose retains adjacent warnings, e.g. pre-washed produce.
        rows = [(pair,) for pair in pairs] if pairs else [(chunk.text,)]
    units = []
    for quotes in rows:
        support = tuple(Support(chunk_id=chunk.chunk_id, support_quote=q) for q in quotes if q)
        text = whitespace(" ".join(s.support_quote for s in support))
        payload = {
            "document_id": chunk.document_id,
            "chunk_id": chunk.chunk_id,
            "claim_text": text,
            "evidence": [s.model_dump() for s in support],
        }
        units.append(EvidenceUnit(unit_id=stable_id("evidence_unit", payload), **payload))
    return units


def parse_aspects(question):
    q = normalize(question)
    foods = tuple(name for name, pattern in FOODS.items() if re.search(pattern, q))
    # The reference question can name food outside the supported ontology. Never
    # substitute a familiar category just because its evidence ranks highly.
    unknown_food = re.search(r"\b(?:raw|cooked|fresh)\s+([a-z]+)\b", q)
    if not foods and unknown_food:
        foods = (unknown_food[1],)
    topics = []
    duration = bool(
        re.search(
            r"\b(?:how long|how many (?:days|months)|storage time|storage duration|keep.*days)\b", q
        )
    )
    if (
        re.search(r"\brest\b", q)
        and re.search(r"\bcook\w*\b", q)
        and not re.search(r"refrigerat|freez|frozen", q)
    ):
        duration = False
    if duration and not re.search(r"\b(?:out|picnic|room temperature|90\s*°?\s*f)\b", q):
        methods = []
        if re.search(r"\b(?:refrigerat\w*|fridge)\b", q):
            methods.append("refrigerator")
        if re.search(r"\b(?:freez\w*|frozen)\b", q):
            methods.append("freezer")
        details = []
        if not foods:
            details.append("food or food category")
        if not methods:
            details.append("refrigerator or freezer storage")
        if (
            foods
            and any(f in foods for f in ("eggs", "beef", "pork", "lamb", "chicken", "turkey"))
            and not re.search(r"\b(?:raw|cooked|fresh|hard cooked|leftovers|unopened|opened)\b", q)
        ):
            details.append("raw/cooked state and opened/unopened packaging where relevant")
        if (
            "eggs" in foods
            and re.search(r"\b(?:raw|fresh|cooked)\b", q)
            and not re.search(r"\b(?:hard cooked|shell|yolks?|whites?|liquid|pasteurized)\b", q)
        ):
            details.append(
                "egg preparation: hard-cooked, in-shell, separated yolks/whites, or liquid pasteurized"
            )
        topics.extend(("storage_duration", method, tuple(details)) for method in methods or [None])
    elif re.search(r"\b(?:picnic|perishable.*out|90\s*°?\s*f|room temperature|hot weather)\b", q):
        topics.append(("time_out", None, ()))
    if re.search(r"\b(?:temperature|how hot|heat\w*|heated|rest)\b", q) and re.search(
        r"\b(?:cook\w*|grill\w*|internal|deli|rest)\b", q
    ):
        topics.append(("cooking", None, () if foods else ("food or food category",)))
    if re.search(r"\b(?:label|daily value|%dv|serving size)\b", q):
        topics.append(("label", None, ()))
    if "pregnan" in q and foods:
        topics.append(("pregnancy_choices", None, ()))
    if re.search(r"\b(?:wash\w*|soap|detergent|pre.washed|ready.to.eat)\b", q):
        topics.append(("washing", None, ()))
    if re.search(r"\b(?:restaurant|dining|special requests|sodium)\b", q):
        topics.append(("dining", None, ()))
    if not topics and re.search(
        r"\b(?:food groups|balanced|healthy eating|vegetables|dietary guidelines|choose|selection)\b",
        q,
    ):
        topics.append(("food_selection", None, ()))
    if not topics and re.search(
        r"\b(?:store|storage|refrigerate|leftovers|handling|cross.contamination|thaw|freez|separat\w*)\b",
        q,
    ):
        topics.append(("handling", None, ()))
    if not topics:
        topics.append(("unknown", None, ()))
    qualifiers = tuple(
        term
        for term in (
            "ground",
            "whole",
            "hard cooked",
            "raw",
            "cooked",
            "fresh",
            "unopened",
            "opened",
            "pre-washed",
            "ready-to-eat",
            "deli",
        )
        if re.search(r"\b" + re.escape(term) + r"\b", q)
    )
    aspects = []
    for index, (topic, method, details) in enumerate(topics):
        payload = {
            "topic": topic,
            "method": method,
            "terms": foods + qualifiers,
            "temperatures": tuple(re.findall(r"\b(\d+)\s*°\s*f\b", q)),
            "required_details": details,
            "description": f"{topic.replace('_', ' ')}{(' in the ' + method) if method else ''}",
        }
        aspects.append(Aspect(aspect_id=f"aspect_{index}", **payload))
    return aspects


def matches(unit, aspect, question):
    text = normalize(unit.claim_text)
    q = normalize(question)
    topic = aspect.topic
    if topic == "unknown" or aspect.required_details:
        return False
    if any(not re.search(r"\b" + re.escape(t) + r"\s*°\s*f\b", text) for t in aspect.temperatures):
        return False
    food_terms = [
        t
        for t in aspect.terms
        if t in FOODS
        or t
        not in (
            "ground",
            "whole",
            "hard cooked",
            "raw",
            "cooked",
            "fresh",
            "unopened",
            "opened",
            "pre-washed",
            "ready-to-eat",
            "deli",
        )
    ]
    if food_terms and not all(
        re.search(FOODS.get(t, r"\b" + re.escape(t) + r"\b"), text) for t in food_terms
    ):
        return False
    for term in aspect.terms:
        if term in (
            "ground",
            "hard cooked",
            "cooked",
            "raw",
            "fresh",
            "unopened",
            "opened",
            "deli",
        ) and not re.search(r"\b" + re.escape(term) + r"\b", text):
            # For cooking, raw milk choice needs raw; raw meat cooking can have
            # source category headings without repeating raw.
            if not (topic == "cooking" and term in ("raw", "fresh", "cooked")):
                return False
    if (
        "ground" not in aspect.terms
        and any(f in aspect.terms for f in ("beef", "pork", "lamb"))
        and "ground" in text
        and "whole" not in text
    ):
        return False
    if topic == "storage_duration":
        method = aspect.method
        if method == "refrigerator":
            line = re.search(
                r"refrigerator[^:]*:\s*([^\n]+)",
                normalize("\n".join(s.support_quote for s in unit.evidence)),
            )
        else:
            line = re.search(
                r"freezer[^:]*:\s*([^\n]+)",
                normalize("\n".join(s.support_quote for s in unit.evidence)),
            )
        if line:
            return "[blank in source]" not in line[1] and bool(
                re.search(r"\d|don.t freeze", line[1])
            )
        return bool(re.search(r"\d+.{0,12}(?:days?|months?)", text)) and bool(
            re.search(r"refrigerat|fridge" if method == "refrigerator" else r"freez|frozen", text)
        )
    if topic == "cooking":
        if "rest" in q and not ("rest" in text and re.search(r"\d+\s*minutes?", text)):
            return False
        return bool(re.search(r"\d+\s*°\s*f\b", text))
    if topic == "time_out":
        return (
            bool(re.search(r"\d+\s*hours?", text))
            and bool(re.search(r"out|90\s*°?\s*f|room temperature", text))
            and ("90" not in q or "90" in text)
        )
    if topic == "label":
        if "serving size" in q:
            return "serving size" in text and "not a recommendation" in text
        if "low" in q or "high" in q:
            return "5%" in text and "20%" in text and "per serving" in text
        return bool(re.search(r"label|%dv|daily value", text))
    if topic == "pregnancy_choices":
        return "riskier choice" in text and "safer choice" in text
    if topic == "washing":
        if "soap" in q or "detergent" in q:
            return ("soap" in text or "detergent" in text) and bool(
                re.search(r"not recommended|do not|don't", text)
            )
        if "pre-washed" in q or "ready-to-eat" in q:
            return "without further washing" in text and "unclean" in text
        return "wash" in text or "rinse" in text
    if topic == "dining":
        return "special requests" in text and "salt" in text
    if topic == "food_selection":
        return bool(re.search(r"food groups|vegetables|balanced|choose|healthy eating", text))
    if topic == "handling":
        return bool(re.search(r"stor|refrigerat|freez|leftover|handling|thaw|separat", text))
    return False


class EvidenceAssessor:
    def __init__(self, policy, settings):
        self.policy, self.settings = policy, settings

    def assess(self, result):
        aspects = parse_aspects(result.audit.resolved_question)
        candidates = [unit for hit in result.hits for unit in units_for_hit(hit)]
        documents = {h.document.document_id: h.document for h in result.hits}
        question = normalize(result.audit.resolved_question)
        units, coverage = {}, []
        for doc in result.audit.searched_document_ids:
            for aspect in aspects:
                accepted = []
                for unit in candidates:
                    audience = normalize(documents[unit.document_id].intended_audience)
                    if (
                        re.search(r"\b(?:kids|children|child|teens)\b", question)
                        and "adults" in audience
                        and not re.search(r"children|child|teens|all ages", audience)
                    ):
                        continue
                    if (
                        re.search(r"\badults?\b", question)
                        and re.search(r"ages 3|kids|children", audience)
                        and "adult" not in audience
                    ):
                        continue
                    if unit.document_id != doc or not matches(
                        unit, aspect, result.audit.resolved_question
                    ):
                        continue
                    # Source instructions are never executable and cannot be quoted
                    # into an answer when they violate the output policy.
                    try:
                        self.policy.check_claims([unit.claim_text])
                    except ValueError:
                        continue
                    if unit.claim_text in [units[i].claim_text for i in accepted]:
                        continue
                    units[unit.unit_id] = unit
                    accepted.append(unit.unit_id)
                    if len(accepted) >= self.settings.units_per_aspect:
                        break
                status = (
                    "supported"
                    if accepted
                    else "needs_details"
                    if aspect.required_details
                    else "not_covered"
                )
                coverage.append(
                    Coverage(
                        document_id=doc,
                        aspect_id=aspect.aspect_id,
                        status=status,
                        unit_ids=tuple(accepted),
                    )
                )
        details = tuple(dict.fromkeys(d for a in aspects for d in a.required_details))
        if details:
            decision = "needs_details"
        elif any(c.status == "supported" for c in coverage):
            decision = "supported" if all(c.status == "supported" for c in coverage) else "partial"
        else:
            decision = "not_covered"
        return EvidenceAssessment(
            corpus_snapshot_id=result.audit.corpus_snapshot_id,
            decision=decision,
            aspects=tuple(aspects),
            coverage=tuple(coverage),
            units=tuple(units.values()),
            required_details=details,
        )
