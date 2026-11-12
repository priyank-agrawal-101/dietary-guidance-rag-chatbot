"""Only layouts and exclusion masks reviewed for the selected HTML corpus."""

from dataclasses import dataclass


@dataclass(frozen=True)
class HTMLProfile:
    registry_profile: str
    root: str
    sections: tuple[str, ...] = ()
    excluded_sections: tuple[str, ...] = ()
    excluded_text: tuple[str, ...] = ()
    retained_text: tuple[str, ...] = ()
    expected_tables: int = 0
    legacy_td_headers: bool = False
    duplicate_text: tuple[str, ...] = ()


MDH = "main .contentLinks .field--name-body > .field__item"
CDC = "main #content .cdc-dfe-body__center"
FDA = "main #main-content .col-md-8 > .inset-column"

PROFILES = {
    "mn_cold_storage": HTMLProfile("mdh_guidance", MDH),
    "mn_safe_temperatures": HTMLProfile(
        "mdh_guidance",
        MDH,
        excluded_text=(r"no longer pink", r"145\s*°?\s*F"),
    ),
    "mn_storage_chart": HTMLProfile("mdh_table", MDH, expected_tables=1, legacy_td_headers=True),
    "mn_food_thermometers": HTMLProfile("mdh_guidance", MDH),
    "cdc_food_safety": HTMLProfile(
        "cdc_guidance",
        CDC,
        sections=("Prevention steps and strategies", "Recalled food"),
    ),
    "cdc_pregnancy_food_safety": HTMLProfile(
        "html_main_content",
        CDC,
        sections=("Safer food choices",),
        excluded_text=(r"fish that contain (?:high|low) amounts of mercury",),
        expected_tables=1,
    ),
    "cdc_emergency_food_safety": HTMLProfile(
        "cdc_consumer_guidance",
        CDC,
        sections=(
            "Prepare for emergencies",
            "Steps to take during a power outage",
            "Steps to take after a power outage",
            "Steps to take after a flood",
        ),
        excluded_sections=(
            "Clean and sanitize food-contact surfaces that have been flooded",
            "How to save commercially prepared food",
        ),
    ),
    "fda_produce": HTMLProfile(
        "html_main_content",
        FDA,
        sections=("Buy Right", "Store Properly", "Separate for Safety", "Prepare Safely"),
    ),
    "fda_seafood": HTMLProfile(
        "html_main_content",
        FDA,
        sections=(
            "Buy Right",
            "Store Properly",
            "Separate for Safety",
            "Picnic Tips",
            "Prepare Safely",
            "Serving",
            "Eating Raw Seafood - What You Need To Know",
        ),
        excluded_text=(r"^Fish\s*:", r"^Shrimp.*Lobster", r"^Clams.*Mussels.*Oysters"),
        duplicate_text=(
            "Follow these serving guidelines once your seafood is cooked and ready to be enjoyed.",
        ),
    ),
    "fda_nutrition_label_current": HTMLProfile(
        "fda_consumer_guidance",
        FDA,
        sections=(
            "1. Serving Information",
            "4. The Percent Daily Value (%DV)",
            "Nutrition Facts Label Variations",
        ),
        # This mixed-purpose page needs a paragraph-level allowlist. Retain source wording.
        retained_text=(
            r"^When looking at the Nutrition Facts label",
            r"^The % Daily Value",
            r"^The %DV (?:shows|helps)",
            r"^Do you need to know how to calculate percentages",
            r"^General Guide to %DV",
            r"^5% DV or less",
            r"^20% DV or more",
            r"^Compare Foods\s*:",
            r"^Understand Nutrient Content Claims\s*:",
            r"^Many Nutrition Facts labels",
            r"^In addition to dual-column labeling",
            r"^Dual-Column Labels$",
            r"^For certain products",
            r"^Single-Ingredient Sugar labels$",
            r"^Packages and containers of products",
        ),
    ),
}

# Filled from the reviewed registry when these masks were implemented. A changed selection
# needs a new mask review, even if its HTML bytes did not change.
REVIEWED_SELECTIONS = {
    "mn_cold_storage": "mask_review_139a932be538274f9a0f2301b289fa20b72c2af5517d424a9918373a1517d27f",
    "mn_safe_temperatures": "mask_review_d73a54651aad43c4e126f7b56bdd64d8804cb310755d1aab50184ed10d93242c",
    "mn_storage_chart": "mask_review_12229bddd7476d323f1e995bcab01940af6d3f8a70a55405c04de8883fa3d5c2",
    "mn_food_thermometers": "mask_review_c6a57c6e54a9310d4641bf9bc61d4afd7f5b36dcf619cc30d56b794ae300ca3b",
    "cdc_food_safety": "mask_review_237b278152f621e2c1ebe9e7f7cd6946da22aa6083f10fc7b2c3f580d86fdec1",
    "cdc_pregnancy_food_safety": "mask_review_815a811c89dff62fc14cf854108eaac4e3da4243186f436e7c04a81bb01e9542",
    "cdc_emergency_food_safety": "mask_review_f4d2365730e3db99b31be3f4e7061d44c3cf38aaa7e51506e1a30101fb5b8845",
    "fda_produce": "mask_review_9ce4118bc5f6163ee80c779b2665cc29d2f796a4d3d3685c7795ad61ed7425f8",
    "fda_seafood": "mask_review_cc90923d8601c14cc32ceea82e3a222f7090b788785626449be7e37f194faf72",
    "fda_nutrition_label_current": "mask_review_b557f559f4156c049d2a88e6f2cd82c1fc6682ba1c3dfdddb3a0f1a7bfe685bf",
}
