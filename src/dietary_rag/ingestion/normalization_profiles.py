"""Scoped exclusions and reviewed spelling repairs, never general medical keyword filters."""

# Conservative entire-block masks: never remove a clause and leave a misleading fragment.
COMMON_MASKS = {
    "individual_food_nutrient_lookup": r"\b(?:contains?|provides?|has)\s+\d[\d.,]*\s*(?:mg|mcg|grams?|g|IU)\s+(?:of\s+)?(?:sodium|potassium|protein|vitamin|calcium|iron|fiber)\b",
    "calorie_weight_targets": r"\b(?:calorie|energy)\s+(?:targets?|goals?|calculator)\b|\b(?:body weight|per kilogram|lose weight|weight loss)\b|\b\d[\d,]*\s*calories?\s*(?:per|a|each)\s*day\b",
    "personalized_calculator": r"\b(?:DRI|dietary reference intake)\s+calculator\b|\bdaily servings guide\b|how many calories (?:you|your child) need",
    "supplement_dosing": r"\bsupplements?\b.{0,70}\b(?:dose|dosing|\d+\s*(?:mg|mcg|IU))\b|\b(?:take|receive)\b.{0,50}\b\d+\s*IU\b",
    "medical_treatment": r"\b(?:treat|treatment for|manage your)\s+(?:diabetes|hypertension|disease|condition)\b|\bdisease-specific\b",
}
SOURCE_MASKS = {
    "nhlbi_sodium_tips_pdf": {
        "nhlbi_treatment_callout": r"blood pressure|DASH eating plan|healthy eating, proven results"
    },
    "hhs_adults_older_adults_pdf": {
        "hhs_supplement_clinical": r"supplement|doctor|health care|2,000"
    },
    "hhs_kids_teens_pdf": {"hhs_supplement_clinical": r"supplement|medical guidance|2,000"},
    "dga_2025_2030_pdf": {
        "dga_clinical_targets": r"calorie|per kilogram|body weight|supplement|medical|disease|blood pressure"
    },
    "mn_safe_grilling_pdf": {"unqualified_whole_cut": r"fresh beef, veal, pork"},
    "mn_safe_temperatures": {"visual_doneness": r"no longer pink"},
}

# Each repair requires an actual native word transition onto a later source line.
# Suspended forms such as "low- or" are deliberately absent.
LINE_WRAP_REPAIRS = {
    "nhlbi_sodium_tips_pdf": {
        "vegeta- bles": "vegetables",
        "bar- becue": "barbecue",
        "spar- ingly": "sparingly",
        "salt- containing": "salt-containing",
    },
    "hhs_adults_older_adults_pdf": {
        "whole- grain": "whole-grain",
        "food-security- and-access": "food-security-and-access",
    },
    "hhs_kids_teens_pdf": {
        "whole- grain": "whole-grain",
        "plant- based": "plant-based",
        "non- dairy": "non-dairy",
    },
}
