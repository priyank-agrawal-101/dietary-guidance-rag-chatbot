"""Reviewed, byte-specific native PDF reading regions in top-left page points."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PDFRegion:
    key: str
    bbox: tuple[float, float, float, float]
    section: str | None = None
    kind: str | None = None
    continuation_key: str | None = None


@dataclass(frozen=True)
class PDFTableSpec:
    key: str
    columns: tuple[float, ...]
    boundaries: tuple[float, ...]
    expected_headers: tuple[str, ...]
    header_span: bool = False
    category_rows: bool = False
    standalone_x: float | None = None
    excluded_rows: tuple[str, ...] = ()
    notes: tuple[tuple[float, float, float, float], ...] = ()
    continuation_key: str | None = None
    after_region: str | None = None

    @property
    def bbox(self):
        return self.columns[0], self.boundaries[0], self.columns[-1], self.boundaries[-1]


@dataclass(frozen=True)
class PDFPageProfile:
    regions: tuple[PDFRegion, ...]
    tables: tuple[PDFTableSpec, ...] = ()
    omission: str = "Outside reviewed text regions: media, page furniture or excluded guidance."


@dataclass(frozen=True)
class PDFProfile:
    pages: tuple[PDFPageProfile, ...]
    heading_min_size: float = 14.5
    excluded_paragraphs: tuple[str, ...] = ()
    strict_unprofiled_tables: bool = False


def region(key, box, section=None, kind=None):
    return PDFRegion(key, box, section, kind)


def full(key, y0=35, y1=745, section=None):
    return region(key, (35, y0, 578, y1), section)


def columns(key, top, bottom, section, split=340):
    return (
        region(key + "_left", (35, top, split, bottom), section),
        region(key + "_right", (split, top, 578, bottom), section),
    )


def hhs_food_groups(top, second):
    return (
        full("title_and_intro", 10, top - 5),
        *(
            region(f"food_group_{row}_{col}", (x0, y0, x1, y1))
            for row, (y0, y1) in enumerate(((top, second - 5), (second, 735)))
            for col, (x0, x1) in enumerate(((40, 210), (220, 380), (390, 575)))
        ),
    )


PROFILES = {
    "mn_cross_contamination_pdf": PDFProfile(
        (
            PDFPageProfile(
                (
                    region("intro", (70, 78, 543, 347)),
                    region("clean", (70, 348, 255, 647), "When preparing food"),
                    region(
                        "boards_marinating_produce", (265, 348, 545, 647), "When preparing food"
                    ),
                    region("serving", (70, 650, 543, 708)),
                    region("source_dates", (70, 710, 543, 738), kind="note"),
                )
            ),
        )
    ),
    "mn_safe_grilling_pdf": PDFProfile(
        (
            PDFPageProfile(
                (
                    region("intro", (70, 96, 543, 229)),
                    region("hot_precook_reheat", (70, 233, 359, 654)),
                    region("marinating", (359, 235, 543, 409)),
                    region("facility_qualification", (72, 657, 358, 692), kind="note"),
                    region("source_dates", (70, 699, 543, 730), kind="note"),
                ),
                (
                    PDFTableSpec(
                        "temperatures",
                        (364.2, 475, 539.7),
                        (413.76, 450.6, 487.56, 512.28, 549.24, 586.08, 623.04, 647.76, 684.72),
                        ("Food", "Safe Internal Temperature"),
                        excluded_rows=(r"^Fresh beef, veal, pork",),
                        notes=((72, 657, 358, 692),),
                        after_region="marinating",
                    ),
                ),
            ),
        )
    ),
    "mn_storage_chart_pdf": PDFProfile(
        (
            PDFPageProfile(
                (
                    full("title_caption_qualification", 42, 143),
                    region("source_date", (545, 755, 580, 778), kind="note"),
                ),
                (
                    PDFTableSpec(
                        "storage",
                        (49, 248, 410.9, 582),
                        (145, 174.9, *tuple(round(191.15 + 16.3 * i, 2) for i in range(34))),
                        ("Product", "Refrigerator (Days at 40°F)", "Freezer (Months at 0°F)"),
                        category_rows=True,
                        standalone_x=61,
                        notes=((35, 114, 580, 142),),
                        after_region="title_caption_qualification",
                    ),
                ),
            ),
        )
    ),
    "nhlbi_sodium_tips_pdf": PDFProfile(
        (
            PDFPageProfile(
                (
                    region("title", (30, 63, 340, 195)),
                    region("shopping_preparation", (217, 216, 393, 539)),
                    region("seasoning", (405, 216, 580, 539)),
                    region("dining_caption", (30, 548, 580, 584)),
                    *(
                        region(f"dining_{i}", (x0, 590, x1, 690), "EASY TIPS FOR DINING OUT")
                        for i, (x0, x1) in enumerate(
                            ((45, 125), (130, 240), (245, 350), (355, 450), (455, 580))
                        )
                    ),
                )
            ),
            PDFPageProfile(
                (),
                omission="Entire page excluded: individual-food sodium lookup and clinical/footer content.",
            ),
        )
    ),
    "hhs_adults_older_adults_pdf": PDFProfile(
        (
            PDFPageProfile(hhs_food_groups(375, 625)),
            PDFPageProfile(
                (
                    full("nutrient_caption", 345, 429),
                    full("fiber", 435, 503),
                    full("calcium", 530, 605),
                    full("vitamin_d", 632, 716),
                )
            ),
            PDFPageProfile(
                (
                    full("potassium", 35, 131),
                    full("folate", 153, 240),
                    full("iron", 258, 379),
                    full("b12", 397, 468),
                    region("enjoying_meals", (222, 506, 550, 710)),
                )
            ),
            PDFPageProfile((full("processed_foods"),)),
            PDFPageProfile((region("label_semantics", (70, 58, 368, 282)),)),
            PDFPageProfile((region("healthy_aging", (70, 63, 500, 356)),)),
            PDFPageProfile((full("budget", 78, 742),)),
            PDFPageProfile((full("safety_hydration", 40, 285),)),
        ),
        excluded_paragraphs=(r"calorie.*(?:a day|daily)|2,000|supplement|doctor|health care",),
    ),
    "hhs_kids_teens_pdf": PDFProfile(
        (
            PDFPageProfile(hhs_food_groups(351, 615)),
            PDFPageProfile((full("nutrients", 62, 742),)),
            PDFPageProfile((full("healthy_fats", 40, 138), full("picky_eaters", 408, 745))),
            PDFPageProfile((full("family_habits", 84, 730),)),
            PDFPageProfile((full("processed_foods"),)),
            PDFPageProfile(
                (
                    full("smoothie_intro", 91, 302),
                    region("recipe_left", (70, 303, 345, 548)),
                    region("recipe_optional", (345, 402, 540, 548)),
                    full("recipe_preparation", 555, 690),
                )
            ),
            PDFPageProfile((region("label_semantics", (70, 69, 409, 273)),)),
            PDFPageProfile(
                (region("safety", (42, 50, 387, 234)), full("caffeine_juice", 241, 505))
            ),
        ),
        excluded_paragraphs=(r"calorie.*(?:a day|daily)|2,000|supplement|medical guidance",),
    ),
    "fda_safe_handling_pdf": PDFProfile(
        (
            PDFPageProfile(
                (
                    region("clean", (53, 436, 319, 724)),
                    region("separate", (333, 436, 578, 724)),
                    region("source_date", (500, 747, 562, 762), kind="note"),
                )
            ),
            PDFPageProfile(
                (
                    region("cook", (63, 81, 321, 364)),
                    region("chill", (337, 81, 578, 364)),
                    region("source_date", (506, 707, 567, 720), kind="note"),
                ),
                (
                    PDFTableSpec(
                        "temperatures",
                        (63.5, 201, 322.1),
                        (
                            375.81,
                            416.31,
                            443.45,
                            461.45,
                            488.59,
                            506.59,
                            533.73,
                            560.87,
                            578.87,
                            618.01,
                            636.01,
                            654.01,
                            681.01,
                            698.9,
                        ),
                        ("Food (reviewed column role)", "Safe Minimium Internal Temperatures"),
                        header_span=True,
                        after_region="chill",
                    ),
                ),
            ),
        )
    ),
    "fda_produce_pdf": PDFProfile(
        (
            PDFPageProfile(
                (
                    region("buy_store", (259, 424, 578, 740)),
                    region("source_date", (500, 747, 563, 761), kind="note"),
                )
            ),
            PDFPageProfile(
                (
                    region("separate_prepare", (65, 78, 578, 544)),
                    region("prewashed", (84, 569, 520, 740)),
                    region("source_date", (506, 747, 568, 761), kind="note"),
                )
            ),
            PDFPageProfile((), omission="Excluded illness, symptoms and reporting panel."),
            PDFPageProfile((), omission="Excluded overlapping general handling infographic."),
        )
    ),
}

# DGA section bands: headings span both columns; read each section's left/right
# prose separately instead of reading all left-column sections before all right ones.
dga_pages = [
    PDFPageProfile((), omission="Visual cover and duplicate text layers omitted."),
    PDFPageProfile((), omission="Political/administrative foreword excluded."),
]
for pn, bands in {
    3: (
        (70, 200, "Eat the Right Amount for You"),
        (204, 430, "Prioritize Protein Foods at Every Meal"),
        (437, 549, "Consume Dairy"),
    ),
    4: (
        (76, 292, "Eat Vegetables & Fruits Throughout the Day"),
        (301, 475, "Incorporate Healthy Fats"),
        (485, 715, "Focus on Whole Grains"),
    ),
    5: ((64, 350, "Limit Highly Processed Foods, Added Sugars, & Refined Carbohydrates"),),
    6: ((74, 230, "Limit Alcoholic Beverages"),),
}.items():
    regs = []
    for top, bottom, section in bands:
        head_end = top + (44 if pn == 5 else 24)
        regs.append(full("heading_" + str(top), top, head_end, section))
        regs.extend(columns("body_" + str(top), head_end + 1, bottom, section))
    if pn == 3:
        regs.append(full("gut_health", 552, 711, "Gut Health"))
    if pn == 5:
        regs.append(full("added_sugars", 360, 570, "Added Sugars"))
    if pn == 6:
        regs.append(full("sodium", 239, 470, "Sodium"))
    dga_pages.append(PDFPageProfile(tuple(regs)))
dga_pages.extend(
    (
        PDFPageProfile(
            (),
            omission="Infant feeding, supplements and specialist guidance held for later section review.",
        ),
        PDFPageProfile(
            (
                full("developmental_readiness", 62, 329, "Introducing Food to Infants & Toddlers"),
                full("middle_heading", 338, 360, "Middle Childhood (5–10 Years)"),
                *columns("middle", 361, 480, "Middle Childhood (5–10 Years)", 325),
            )
        ),
        PDFPageProfile(
            (),
            omission="Mixed pregnancy/lactation, clinical and supplement passages held for later section review.",
        ),
        PDFPageProfile(
            (
                full("vegetarian_heading", 224, 246, "Vegetarians & Vegans"),
                region("vegetarian_foods", (35, 248, 322, 410), "Vegetarians & Vegans"),
            ),
        ),
    )
)
PROFILES["dga_2025_2030_pdf"] = PDFProfile(
    tuple(dga_pages),
    excluded_paragraphs=(
        r"calorie|per kilogram|body weight|supplement|medical|disease|blood pressure",
    ),
)

REVIEWED_SOURCES = {
    "mn_cross_contamination_pdf": {
        "raw_sha256": "dd44a22d78752af0e9c7972b32aff5f146808ea86b55a2adeea4ba637d423f8b",
        "selection_id": "pdf_mask_review_07728fda2f9c82efd462fdeaa8d2ecf3789b07c1ab2faf8d2ed0988ffb7d1d60",
        "parser_profile": "pdf_layout",
    },
    "mn_safe_grilling_pdf": {
        "raw_sha256": "4443ecf161554667e600fc9721ff62b26e36f642eeeabbd5b88b8a4764808391",
        "selection_id": "pdf_mask_review_d72c5660ec41ea336d0885c9104b1408995022250be21229e2f2d9b9a63baee7",
        "parser_profile": "pdf_layout",
    },
    "mn_storage_chart_pdf": {
        "raw_sha256": "47d4085e42748549115136c5982416ed09067acb1b9660d0ed39476f619f5e25",
        "selection_id": "pdf_mask_review_768bd3e1780242b9f273414f11310ed3dcb67cd32f103c9be48610fd9c10d594",
        "parser_profile": "pdf_layout",
    },
    "nhlbi_sodium_tips_pdf": {
        "raw_sha256": "30f97a37299f9f6d9d55d9c2f2222820120d3d4d4f9a66ca0bb66c04e0c6f307",
        "selection_id": "pdf_mask_review_db0e96ab5d1fbfa1b11090d76b6a57b0d6dc92ba6279c4567a8f614c63d65f86",
        "parser_profile": "pdf_layout",
    },
    "hhs_adults_older_adults_pdf": {
        "raw_sha256": "503e78136fa6b9f46a967028e25e76de2cdca768c823eb59d59a49870761c8e7",
        "selection_id": "pdf_mask_review_2aa06c9260eecb9e186202ac6d4b822edc1e86274a322930209d41d5beedd9bd",
        "parser_profile": "pdf_layout",
    },
    "hhs_kids_teens_pdf": {
        "raw_sha256": "410363b45c679c2cb956da81cb2d58e3667d85f91369c8618a0b4c6b8cc6a0f3",
        "selection_id": "pdf_mask_review_55ff5a471e87900d2d1fe1c551fd562a3b16f8782ddbb398f41aa18ecd258fae",
        "parser_profile": "pdf_layout",
    },
    "fda_safe_handling_pdf": {
        "raw_sha256": "41d19d5b61e42629b4d3268a03743bd1009ce354f03004cdeb4e1e1ce47133d3",
        "selection_id": "pdf_mask_review_4e4001bf9fabcd477f1bdaef3df52a45de913932f4ab3b1315b6b7352df76e45",
        "parser_profile": "native_pdf",
    },
    "fda_produce_pdf": {
        "raw_sha256": "100be676f649677fd530506d83462d6207bbd94703a023e72cfbf31dc4b26445",
        "selection_id": "pdf_mask_review_8e49820b11703eca4c7f22719d6900be31a950b1205ebdecf80aac54c86a9287",
        "parser_profile": "native_pdf",
    },
    "dga_2025_2030_pdf": {
        "raw_sha256": "f27d3c96d51db4847b6087c068ec2d3dca400ef35e5e94c4c3ac77e8fd66e36c",
        "selection_id": "pdf_mask_review_33754535428003b0051039c827f2da999937dcc17ced8d3c09e43fabfbe1580d",
        "parser_profile": "pdf_layout",
    },
}
