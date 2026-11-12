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
    "melon": r"\b(?:water)?melons?\b",
    "mayonnaise": r"\bmayonnaise\b",
    "deli salad": r"\bdeli salads?\b",
    "soups": r"\b(?:soups?|stews?)\b",
    "juice": r"\b(?:juice|cider)\b",
    "dough": r"\b(?:dough|batter|flour)\b",
    "leftovers": r"\bleftovers?\b",
    "tv dinners": r"\btv dinners?\b",
    "produce": r"\b(?:produce|fruits?|vegetables?|lettuce|mushrooms|cucumbers|salad greens)\b",
}

FOOD_GROUPS = ("Protein foods", "Dairy", "Vegetables", "Fruits", "Healthy fats", "Whole grains")


def food_group_units(hits):
    """Join an introduction to exact group labels, without unrelated card claims."""
    units = []
    for hit in hits:
        if not re.search(r"food groups:\s*$", hit.chunk.text, re.I):
            continue
        pages = {loc.physical_page for loc in hit.chunk.source_locations if loc.format == "pdf"}
        support = [Support(chunk_id=hit.chunk.chunk_id, support_quote=hit.chunk.text)]
        for label in FOOD_GROUPS:
            card = next(
                (
                    candidate
                    for candidate in hits
                    if candidate.chunk.document_version_id == hit.chunk.document_version_id
                    and candidate.chunk.document_id == hit.chunk.document_id
                    and any(
                        re.fullmatch(r"page:\d+:food_group_\d+_\d+", k)
                        for k in candidate.chunk.region_keys
                    )
                    and {loc.physical_page for loc in candidate.chunk.source_locations} <= pages
                    and re.search(r"(?:^|\n)" + re.escape(label) + r"\s*—", candidate.chunk.text)
                ),
                None,
            )
            if card is None:
                break
            support.append(Support(chunk_id=card.chunk.chunk_id, support_quote=label))
        if len(support) != len(FOOD_GROUPS) + 1:
            continue
        payload = {
            "document_id": hit.chunk.document_id,
            "chunk_id": hit.chunk.chunk_id,
            "claim_text": whitespace(support[0].support_quote)
            + " "
            + ", ".join(s.support_quote for s in support[1:])
            + ".",
            "evidence": [s.model_dump() for s in support],
        }
        units.append(EvidenceUnit(unit_id=stable_id("evidence_unit", payload), **payload))
    return units


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
    q = normalize(question).replace("hard-cooked", "hard cooked")
    foods = tuple(name for name, pattern in FOODS.items() if re.search(pattern, q))
    # A prepared mixed-food row is its own category, not raw/shell egg storage.
    if "deli salad" in foods:
        foods = ("deli salad",)
    # The reference question can name food outside the supported ontology. Never
    # substitute a familiar category just because its evidence ranks highly.
    unknown_food = re.search(r"\b(?:raw|cooked|fresh)\s+([a-z]+)\b", q)
    if not foods and unknown_food:
        foods = (unknown_food[1],)
    topics = []
    if re.search(
        r"\b(?:cooking|edible|olive|vegetable|canola|sunflower|coconut|mustard) oils?\b", q
    ):
        topics.append(("cooking_oils", None, ()))
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
    if re.search(r"\b(?:power (?:cut|outage)|electricity|without power)\b", q):
        topics.append(("outage", None, ()))
        duration = False
    if re.search(r"\bmicrowav\w*\b", q):
        topics.append(("microwave", None, ()))
    if re.search(r"\b(?:probe |food )?thermometer\b", q) and re.search(
        r"\b(?:probe|place|where|clean|uses)\b", q
    ):
        topics.append(("thermometer", None, ()))
    if re.search(r"\b(?:hydration|hydrated|drinks?|water)\b", q) and re.search(
        r"\b(?:adults?|guide|hydration|hydrated)\b", q
    ):
        topics.append(("hydration", None, ()))
    if re.search(r"\b(?:dairy|caffeinat\w*)\b", q):
        topics.append(("dairy" if "dairy" in q else "caffeine", None, ()))
    if duration and not re.search(r"\b(?:out|picnic|room temperature|90\s*°?\s*f)\b", q):
        methods = []
        if re.search(r"\b(?:refrigerat\w*|fridge|chilled|chilling)\b", q):
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
    if re.search(r"\b(?:labels?|daily values?|%dv|serving sizes?)\b", q):
        topics.append(("label", None, ()))
    if "pregnan" in q and foods:
        topics.append(("pregnancy_choices", None, ()))
    if re.search(r"\b(?:wash\w*|scrub\w*|soap|detergent|pre.washed|ready.to.eat)\b", q):
        topics.append(("washing", None, ()))
    if re.search(r"\b(?:restaurant|dining|special requests)\b", q):
        topics.append(("dining", None, ()))
    elif re.search(r"\b(?:sodium|salt|salty|herbs?|citrus|seasonings?|flavorings)\b", q):
        topics.append(("sodium", None, ()))
    if re.search(
        r"\b(?:buy\w*|shop\w*|grocery|affordable|in season|out of season)\b", q
    ) and re.search(r"\b(?:produce|watermelon|salad greens|vegetables?|fruits?)\b", q):
        topics.append(("produce_selection", None, ()))
    if re.search(r"\b(?:cold|refrigerat\w*|fridge)\b", q) and re.search(
        r"\b(?:lettuce|mushrooms|pre-cut|packaged produce)\b", q
    ):
        topics.append(("produce_storage", None, ()))
    if (
        re.search(r"\b(?:hot|warm)\b", q)
        and re.search(r"\b(?:portions?|containers?|leftovers?)\b", q)
        and not duration
    ):
        topics.append(("hot_leftovers", None, ()))
    if not topics and re.search(
        r"\b(?:food groups|balanced|healthy eating|vegetables|dietary guidelines|choose|selection)\b",
        q,
    ):
        topics.append(("food_selection", None, ()))
    if not topics and re.search(
        r"\b(?:store|storage|refrigerat\w*|leftovers|handling|cross.contamination|thaw\w*|defrost\w*|refreez\w*|freez\w*|frozen|separat\w*)\b",
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
    text = normalize(unit.claim_text).replace("hard-cooked", "hard cooked")
    q = normalize(question).replace("hard-cooked", "hard cooked")
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
    if (
        topic not in {"outage", "thermometer"}
        and food_terms
        and not all(
            re.search(FOODS.get(t, r"\b" + re.escape(t) + r"\b"), text)
            or (
                topic == "microwave"
                and t == "leftovers"
                and "when reheating" in text
                and "microwaved food" in text
            )
            for t in food_terms
        )
    ):
        return False
    if topic in {
        "outage",
        "microwave",
        "thermometer",
        "hydration",
        "dairy",
        "caffeine",
        "sodium",
        "produce_selection",
        "produce_storage",
        "hot_leftovers",
        "cooking_oils",
    }:
        return matches_guidance(text, q, topic)
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
        if re.search(r"compar\w*", q):
            return (
                "compare" in text
                and "serving size is the same" in text
                and "higher in nutrients" in text
                and "lower in nutrients" in text
            )
        if re.search(r"dual.column|per-serving|per-package", q):
            return (
                bool(re.search(r"dual.column", text))
                and "per serving" in text
                and "per package" in text
                and "entire package" in text
            )
        if re.search(r"honey|maple syrup|single.ingredient", q):
            return (
                "not required" in text
                and "grams of added sugars" in text
                and "must still include" in text
                and "percent daily value" in text
            )
        if re.search(r"serving sizes?", q):
            return "serving size" in text and "not a recommendation" in text
        if "low" in q or "high" in q:
            return "5%" in text and "20%" in text and "per serving" in text
        return bool(re.search(r"label|%dv|daily value", text))
    if topic == "pregnancy_choices":
        return "riskier choice" in text and "safer choice" in text
    if topic == "washing":
        if "scrub" in q:
            return (
                "scrub" in text and "produce brush" in text and ("dry" not in q or "towel" in text)
            )
        if "soap" in q or "detergent" in q:
            return ("soap" in text or "detergent" in text) and bool(
                re.search(r"not recommended|do not|don't", text)
            )
        if "pre-washed" in q or "ready-to-eat" in q:
            return "without further washing" in text and "unclean" in text
        return "wash" in text or "rinse" in text
    if topic == "dining":
        if re.search(r"menu|online|research", q):
            return "menu" in text and "online nutrition information" in text and "avoid" in text
        return "special requests" in text and "salt" in text
    if topic == "food_selection":
        if re.search(r"food groups|variety of healthy foods", q):
            return "food groups:" in text and all(normalize(label) in text for label in FOOD_GROUPS)
        return bool(re.search(r"food groups|vegetables|balanced|choose|healthy eating", text))
    if topic == "handling":
        if re.search(r"thaw|defrost", q):
            return (
                "thaw" in text
                and "refrigerator" in text
                and "cold water" in text
                and "microwave" in text
                and "never thaw" in text
            )
        return bool(re.search(r"stor|refrigerat|freez|leftover|handling|thaw|separat", text))
    return False


def matches_guidance(text, question, topic):
    """Topic-specific completeness over one intact evidence unit, never model knowledge."""
    if topic == "cooking_oils":
        if re.search(r"smoke point|temperatures?|how hot|reus\w*|deep.fry\w*|frying", question):
            return False
        requested = re.findall(
            r"\b(olive|vegetable|canola|sunflower|coconut|mustard|peanut|sesame|avocado|soybean|corn) (?:cooking )?oils?\b",
            question,
        )
        return (
            all(re.search(r"\b" + oil + r" oils?\b", text) for oil in requested)
            and "when cooking with or adding fats" in text
            and "prioritize oils with essential fatty acids" in text
            and "olive oil" in text
        )
    if topic == "microwave":
        if re.search(r"stand|standing", question):
            return "standing times" in text and "cold spots" in text and "hotter areas" in text
        return (
            "wattage" in text
            and "watts" in text
            and "minimum cooking time" in text
            and "maximum cooking time" in text
            and "thermometer" in text
            and "165" in text
        )
    if topic == "thermometer":
        return (
            "thickest part" in text
            and "away from bone" in text
            and "fat" in text
            and "gristle" in text
            and "several places" in text
            and ("clean" not in question or "soap and water" in text)
        )
    if topic == "outage":
        if re.search(r"refreez|ice crystals|electricity returns", question):
            return (
                "ice crystals" in text
                and "refreeze" in text
                and "never taste" in text
                and bool(re.search(r"40\s*°\s*f or below", text))
            )
        return (
            "doors" in text
            and "closed" in text
            and bool(re.search(r"(?<![-\w])full freezer", text))
            and "half-full freezer" in text
            and bool(re.search(r"40\s*°\s*f or below", text))
        )
    if topic == "hydration":
        return "water" in text and "unsweetened drinks" in text and "water in foods" in text
    if topic == "dairy":
        return (
            "dairy" in text
            and "milk" in text
            and "fortified dairy alternatives" in text
            and (not re.search(r"kids|children|under", question) or "age 10 and under" in text)
        )
    if topic == "caffeine":
        return (
            "caffeinated drinks" in text
            and "age 10 and younger" in text
            and "older kids" in text
            and "avoid energy drinks" in text
        )
    if topic == "sodium":
        if re.search(r"affordable|out of season|in season", question) and re.search(
            r"produce|vegetables?|fruits?", question
        ):
            return matches_guidance(text, question, "produce_selection")
        if re.search(r"herbs|citrus|seasonings|flavor", question):
            return "herbs" in text and "salt-free seasoning" in text and "instead of salt" in text
        if re.search(r"canned tuna|beans|rice|pasta|rinse|remove", question):
            return (
                "rinsed" in text and "remove some of the sodium" in text and "without salt" in text
            )
        if re.search(r"vegetables?", question):
            return (
                "vegetables" in text
                and "low-sodium or no-salt-added" in text
                and "season them yourself" in text
            )
        return False
    if topic == "produce_selection":
        if re.search(r"affordable|out of season|in season", question):
            return (
                "in season" in text
                and "frozen, dried, or canned" in text
                and "little or no added sugars or sodium" in text
            )
        return (
            "pre-cut" in text
            and "refrigerated or surrounded by ice" in text
            and "separately from raw meat" in text
        )
    if topic == "produce_storage":
        return "refrigerator thermometer" in text and "40" in text and "pre-cut or packaged" in text
    if topic == "hot_leftovers":
        return "shallow containers" in text and "small portions of hot food" in text
    return False


class EvidenceAssessor:
    def __init__(self, policy, settings):
        self.policy, self.settings = policy, settings

    def assess(self, result):
        aspects = parse_aspects(result.audit.resolved_question)
        candidates = [unit for hit in result.hits for unit in units_for_hit(hit)]
        candidates = food_group_units(result.hits) + candidates
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
