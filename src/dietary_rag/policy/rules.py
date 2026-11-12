"""Conservative request/claim patterns, normalized before scope decisions.

Words such as fat, calories, and weight alone never prohibit ordinary guidance.
Unrecognized phrasing is left to the typed classifier, never implicitly allowed.
"""

import re
import unicodedata


def normalize(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return text.replace("’", "'").replace("–", "-").replace("—", "-")


PATTERNS = {
    "policy_bypass": (
        r"\b(?:ignore|bypass|override|disable|forget)\b.{0,60}\b(?:policy|rules|restrictions|safety|instructions|scope)\b",
        r"\b(?:pretend|roleplay|act as)\b.{0,40}\b(?:doctor|dietitian|physician)\b",
    ),
    "medical_advice": (
        r"\b(?:treats?|cures?|diagnose|manage|reverse|heal|control|lower)\b.{0,70}\b(?:diabetes|disease|cancer|blood pressure|cholesterol|condition|symptoms|infection|hypertension|blood sugar)\b",
        r"\b(?:i have|i've got|diagnosed with|my)\b.{0,45}\b(?:diabetes|kidney disease|ckd|cancer|hypertension|symptoms|blood sugar)\b",
        r"\b(?:diet|meal plan|eat|foods?|nutrition)\b.{0,40}\b(?:for|with)\b.{0,20}\b(?:diabetes|diabetic|kidney disease|ckd|cancer|hypertension|ibs)\b",
        r"\b(?:vomiting|diarrhea|stomach pain|fever|food poisoning)\b.{0,70}\b(?:should i|what should|what do|mean|treat|help|medicine)\b",
        r"\b(?:should i|what should|what do|do i have)\b.{0,70}\b(?:vomiting|diarrhea|stomach pain|food poisoning)\b",
        r"\b(?:dose|dosage|medication|insulin|prescribe)\b",
    ),
    "calorie_weight_target": (
        r"\b(?:how many|how much|what|calculate|set|give me|recommend)\b.{0,60}\b(?:calories?|kcal|caloric)\b.{0,60}\b(?:i|me|my|per day|daily|target|allowance|budget|limit)\b",
        r"\b(?:my|personal|daily|recommended)\b.{0,25}\b(?:calorie|caloric|kcal)\b.{0,25}\b(?:target|budget|allowance|limit|intake|needs)\b",
        r"\b(?:lose|losing|gain|gaining|reduce|shed|drop)\b.{0,25}\b(?:weight|pounds?|kilograms?|kg|lbs?|kilos)\b",
        r"\b(?:goal|target|ideal|healthy)\s+(?:body\s+)?weight\b",
        r"\b(?:what|how much)\b.{0,35}\b(?:should|ought to)\b.{0,25}\bweigh\b",
        r"\b(?:slim down|bulk up|get thinner|get skinny|cutting diet)\b",
    ),
    "individual_food_nutrients": (
        r"\b(?:how much|how many|grams? of|amount of|content of)\b.{0,45}\b(?:protein|fat|carb(?:ohydrate)?s?|sodium|sugar|fiber|fibre|calories?|kcal|vitamin|mineral|potassium|calcium|iron)\b.{0,65}\b(?:in|does|has|have|per)\b",
        r"\b(?:nutrient|nutrition|nutritional)\s+(?:values?|content|facts|profile)\b.{0,35}\b(?:of|for|in)\b",
        r"\b(?:banana|apple|egg|rice|chicken|milk|salmon|bread|broccoli)\b(?![^?.]{0,35}\b(?:remove|reduce|avoid|limit|rinse|wash)\b).{0,35}\b(?:protein|carbs?|calories?|sodium|fat)\s*(?:content|amount|grams|count)?\s*\?*$",
    ),
}


def restricted_intents(text):
    value = normalize(text)
    return tuple(
        kind for kind, patterns in PATTERNS.items() if any(re.search(p, value) for p in patterns)
    )
