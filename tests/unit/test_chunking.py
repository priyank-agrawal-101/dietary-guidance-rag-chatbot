"""Chunk boundary, condition, fidelity and provenance regressions."""

import json
import re

import pytest
from conftest import review_chunk_fixture
from pydantic import ValidationError

from dietary_rag.corpus.identity import stable_id
from dietary_rag.ingestion.chunk_contracts import ChunkBuild
from dietary_rag.ingestion.chunk_tokenizer import E5Tokenizer, load_manifest, prepare_tokenizer
from dietary_rag.ingestion.chunking import (
    StructureChunker,
    list_ranges,
    sentence_ranges,
    validate_chunk_links,
)
from dietary_rag.ingestion.html_profiles import PROFILES as HTML_PROFILES
from dietary_rag.ingestion.normalization import Normalizer
from dietary_rag.ingestion.pdf_profiles import PROFILES as PDF_PROFILES


class CounterTokenizer:
    limit = 512
    metadata = {"model_name": "explicit-test-word-counter", "revision": "fixture"}

    def count(self, text, *, special_tokens=True):
        return len(re.findall(r"\w+|[^\w\s]", text)) + (2 if special_tokens else 0)


def synthetic(normalizer, native, rows):
    template = next(b for b in native.blocks if b.block_type == "paragraph")
    blocks = tuple(
        template.model_copy(
            update={
                "block_id": f"block_chunk_fixture_{i}",
                "sequence_index": i,
                "block_type": kind,
                "text": text,
                "heading_path": ("Fixture section",),
                "source_locations": tuple(
                    loc.model_copy(update={"dom_path": f"body > p:nth-of-type({i + 1})"})
                    for loc in template.source_locations
                ),
            }
        )
        for i, (kind, text) in enumerate(rows)
    )
    modified = native.model_copy(update={"blocks": blocks, "tables": ()})
    payload = modified.model_dump(exclude={"extraction_id", "review_state"})
    modified = modified.model_copy(update={"extraction_id": stable_id("html_extraction", payload)})
    return modified, review_chunk_fixture(normalizer, modified)


def build_fixture(chunk_evidence, name="mn_cold_storage", tokenizer=None, **kwargs):
    normalizer, native, norm = chunk_evidence(name)
    chunker = StructureChunker(tokenizer or CounterTokenizer(), normalizer.registry, **kwargs)
    return chunker, chunker.build(norm, native), norm, native


@pytest.mark.parametrize("name", [*HTML_PROFILES, *PDF_PROFILES])
def test_native_sources_have_exact_coverage_and_stable_ids(chunk_evidence, name):
    normalizer, native, norm = chunk_evidence(name)
    role = "comparison" if norm.comparison_only else "consumer"
    chunker = StructureChunker(CounterTokenizer(), normalizer.registry, role=role)
    build = chunker.build(norm, native)
    assert build.state == "ready"
    assert build == chunker.build(norm, native)
    assert ChunkBuild.model_validate_json(build.model_dump_json()) == build
    validate_chunk_links(build, norm, chunker.tokenizer)
    heading_ids = {b.block_id for b in norm.blocks if b.block_type == "heading"}
    assert not any(s.block_id in heading_ids for c in build.chunks for s in c.spans)
    assert all(c.token_count <= 512 for c in build.chunks)


def test_sentence_overlap_is_complete_and_conditions_are_atomic(chunk_evidence):
    normalizer, native, norm = chunk_evidence("mn_cold_storage")
    text = " ".join(
        f"Advice {i} includes complete supporting explanation and qualifications."
        for i in range(18)
    )
    text += " Refrigerate within two hours. Reduce that to one hour when above 90 F."
    native, norm = synthetic(normalizer, native, [("paragraph", text)])
    tokenizer = CounterTokenizer()
    chunker = StructureChunker(tokenizer, normalizer.registry, target_tokens=80, overlap_tokens=25)
    build = chunker.build(norm, native)
    assert len(build.chunks) > 3
    assert any(s.overlap for c in build.chunks for s in c.spans)
    expected = set(sentence_ranges(text))
    for c in build.chunks:
        assert all((s.start, s.end) in expected for s in c.spans)
        overlap = [text[s.start : s.end] for s in c.spans if s.overlap]
        assert tokenizer.count("\n\n".join(overlap), special_tokens=False) <= 25
    containing = [c for c in build.chunks if "Reduce that" in c.text]
    assert containing and all("Refrigerate within two hours." in c.text for c in containing)
    changed = chunker.build(
        *reversed(synthetic(normalizer, native, [("paragraph", text + " New advice.")]))
    )
    assert changed.build_id != build.build_id


def test_split_numbered_list_repeats_both_introductions_and_nested_explanations(chunk_evidence):
    normalizer, native, norm = chunk_evidence("mn_cold_storage")
    items = "\n".join(
        f"{i}. Keep complete advice with its condition.\n  - Do not freeze; this affects quality, not safety."
        for i in range(1, 9)
    )
    native, norm = synthetic(
        normalizer,
        native,
        [
            ("paragraph", "For home food safety:"),
            ("recommendation_list", "Follow these recommendations:\n" + items),
        ],
    )
    chunker = StructureChunker(
        CounterTokenizer(), normalizer.registry, target_tokens=95, overlap_tokens=20
    )
    build = chunker.build(norm, native)
    assert build.state == "ready" and len(build.chunks) >= 3
    assert all(
        "For home food safety:" in c.text and "Follow these recommendations:" in c.text
        for c in build.chunks
    )
    for i in range(1, 9):
        found = [c for c in build.chunks if f"{i}. Keep complete" in c.text]
        assert (
            len(found) == 1
            and "  - Do not freeze; this affects quality, not safety." in found[0].text
        )
    assert not any(s.overlap for c in build.chunks for s in c.spans)


def test_adjacent_condition_paragraph_does_not_split_at_preferred_target(chunk_evidence):
    normalizer, native, norm = chunk_evidence("mn_cold_storage")
    native, norm = synthetic(
        normalizer,
        native,
        [
            (
                "paragraph",
                "Refrigerate perishable food within two hours. " + "Supporting detail. " * 10,
            ),
            ("paragraph", "If exposed to temperatures above 90 F, reduce that to one hour."),
        ],
    )
    build = StructureChunker(
        CounterTokenizer(), normalizer.registry, target_tokens=65, overlap_tokens=20
    ).build(norm, native)
    assert any("two hours" in c.text and "one hour" in c.text for c in build.chunks)


@pytest.mark.parametrize("kind", ["paragraph", "recommendation_list"])
def test_oversized_atomic_evidence_quarantines_without_truncation(chunk_evidence, kind):
    normalizer, native, norm = chunk_evidence("mn_cold_storage")
    text = ("1. " if kind == "recommendation_list" else "") + "evidence " * 600
    native, norm = synthetic(normalizer, native, [(kind, text)])
    build = StructureChunker(CounterTokenizer(), normalizer.registry).build(norm, native)
    assert build.state == "quarantined" and build.diagnostics and not build.chunks
    assert "without truncation" in build.diagnostics[0]


@pytest.mark.parametrize(
    "name", ["hhs_adults_older_adults_pdf", "hhs_kids_teens_pdf", "nhlbi_sodium_tips_pdf"]
)
def test_independent_reviewed_cards_never_share_chunks_or_overlap(chunk_evidence, name):
    chunker, build, norm, native = build_fixture(chunk_evidence, name)
    originals = {
        d.normalized_block_id: d.original_block_id for d in norm.decisions if d.state == "retained"
    }
    regions = {layout.block_id: layout.region_key for layout in native.layouts}
    for chunk in build.chunks:
        assert len({regions[originals[s.block_id]] for s in chunk.spans}) <= 1
        if any("food_group" in r or "dining_" in r for r in chunk.region_keys):
            assert not any(s.overlap for s in chunk.spans)
    assert (
        all("blood pressure" not in c.text.casefold() for c in build.chunks)
        if name == "nhlbi_sodium_tips_pdf"
        else True
    )


def test_storage_categories_headers_blanks_notes_and_row_coverage(chunk_evidence):
    chunker, build, norm, native = build_fixture(chunk_evidence, "mn_storage_chart")
    tables = [c for c in build.chunks if c.chunk_type == "table"]
    assert tables and len(tables) < len(norm.tables[0].rows)
    for c in tables:
        assert "Refrigerator (Days at 40°F)" in c.text and "Freezer (Months at 0°F)" in c.text
        assert "taste loss" in c.text and c.table_context.footnotes
        assert len({norm.tables[0].rows[i].category for i in c.table_rows.row_indices}) == 1
    assert any("[blank in source]" in c.text for c in tables)
    assert any("don’t freeze" in c.text for c in tables)


def test_pregnancy_each_category_keeps_both_choices_and_conditions(chunk_evidence):
    chunker, build, norm, native = build_fixture(chunk_evidence, "cdc_pregnancy_food_safety")
    tables = [c for c in build.chunks if c.table_rows]
    assert len(tables) == len(norm.tables[0].rows) == 9
    for c in tables:
        assert (
            len(c.table_rows.row_indices) == 1
            and "Riskier Choice:" in c.text
            and "Safer Choice:" in c.text
        )
        assert "Pregnant" in c.embedding_text and c.table_context.footnotes
    melon = next(c for c in tables if "Cut melon" in c.text)
    assert all(p in melon.text for p in ("2 hours", "1 hour", "90°F", "7 or fewer days"))


def test_original_html_list_siblings_stay_together(chunk_evidence):
    _, build, _, _ = build_fixture(chunk_evidence, "fda_nutrition_label_current")
    dv = [c for c in build.chunks if "5% DV or less" in c.text]
    assert len(dv) == 1 and "20% DV or more" in dv[0].text
    assert len(dv[0].spans) == 2


def test_unreviewed_comparison_and_modified_geometry_are_rejected(chunk_evidence):
    chunker, build, norm, native = build_fixture(chunk_evidence)
    normalizer = Normalizer(chunker.registry)
    with pytest.raises(ValueError, match="reviewed"):
        chunker.build(normalizer.normalize(native), native)
    _, comparison, compare_norm, compare_native = build_fixture(
        chunk_evidence, "mn_storage_chart_pdf", role="comparison"
    )
    compare_normalizer, compare_native, compare_norm = chunk_evidence("mn_storage_chart_pdf")
    with pytest.raises(ValueError, match="comparisons"):
        StructureChunker(chunker.tokenizer, compare_normalizer.registry).build(
            compare_norm, compare_native
        )
    with pytest.raises(ValueError, match="comparison build"):
        StructureChunker(chunker.tokenizer, chunker.registry, role="comparison").build(norm, native)
    modified = build.model_copy(
        update={
            "chunks": (
                build.chunks[0].model_copy(
                    update={
                        "source_locations": (
                            build.chunks[0]
                            .source_locations[0]
                            .model_copy(update={"dom_path": "wrong"}),
                        )
                    }
                ),
                *build.chunks[1:],
            )
        }
    )
    with pytest.raises(ValueError, match="geometry"):
        validate_chunk_links(modified, norm, chunker.tokenizer)
    payload = build.model_dump(mode="json")
    payload["chunks"][0]["text"] += " forged"
    with pytest.raises(ValidationError):
        ChunkBuild.model_validate(payload)


@pytest.mark.parametrize("target,overlap", [(513, 50), (0, 0), (350, 350), (350, -1)])
def test_invalid_budgets_fail(target, overlap, reviewed_registry):
    with pytest.raises(ValueError, match="budget"):
        StructureChunker(
            CounterTokenizer(), reviewed_registry, target_tokens=target, overlap_tokens=overlap
        )


def test_native_bullets_abbreviations_and_decimal_temperatures():
    text = "▪ Advice one.\n  - Nested explanation.\n▪ Advice two."
    assert len(list_ranges(text)) == 2
    assert "Nested explanation" in text[slice(*list_ranges(text)[0])]
    assert len(sentence_ranges("The U.S. recommends 40.5 F. Keep it cold.")) == 2


def test_real_e5_count_includes_prefix_context_cls_sep_and_never_truncates(real_chunk_tokenizer):
    tokenizer = real_chunk_tokenizer
    assert tokenizer.limit == 512
    assert (
        tokenizer.count("passage: Food safety")
        == tokenizer.count("passage: Food safety", special_tokens=False) + 2
    )
    assert tokenizer.count("passage: " + "food " * 700) > 512
    assert tokenizer.count("food") < tokenizer.count(
        "passage: Document: Guidance\nAudience: Consumer\n\nfood"
    )


def test_hash_validation_and_unpinned_revision_fail_closed(tmp_path, real_chunk_tokenizer):
    manifest = dict(real_chunk_tokenizer.metadata)
    manifest.pop("tokenizers_version")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="missing"):
        E5Tokenizer(tmp_path, path)
    (tmp_path / "tokenizer.json").write_text("forged")
    with pytest.raises(ValueError, match="hash"):
        E5Tokenizer(tmp_path, path)
    manifest["revision"] = "main"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="unpinned"):
        load_manifest(path)
    with pytest.raises(ValueError, match="unpinned"):
        prepare_tokenizer(path, tmp_path)


def test_oversized_table_row_is_unavailable_without_cutting_notes(chunk_evidence):
    normalizer, native, norm = chunk_evidence("cdc_pregnancy_food_safety")

    class TableCounter(CounterTokenizer):
        metadata = {"model_name": "explicit-test-table-budget-counter"}

        def count(self, text, *, special_tokens=True):
            return 600 if "Table:" in text else super().count(text, special_tokens=special_tokens)

    build = StructureChunker(TableCounter(), normalizer.registry).build(norm, native)
    assert build.state == "quarantined" and len(build.diagnostics) == 9
    assert not any(c.table_rows for c in build.chunks)


def test_ocr_whole_artifact_quarantine_blocks_even_clear_candidates(tmp_path, reviewed_registry):
    from pathlib import Path

    from dietary_rag.corpus.contracts import DocumentVersion
    from dietary_rag.ingestion.ocr import OCRExtractor
    from dietary_rag.ingestion.ocr_contracts import OCRPlan

    class Engine:
        metadata = {"name": "test", "version": "1", "language": "eng"}

        def recognize(self, path, plan):
            return [
                {
                    "text": "40" if "page-008" in path.name else "Clean",
                    "conf": "91" if "page-008" in path.name else "99",
                    "left": "20",
                    "top": "20",
                    "width": "80",
                    "height": "30",
                    "block_num": "1",
                    "par_num": "1",
                    "line_num": "1",
                }
            ]

    root = Path(__file__).resolve().parents[1] / "fixtures/ocr"
    document = DocumentVersion.model_validate_json((root / "document.json").read_text())
    plan = OCRPlan.model_validate_json((root / "plan.json").read_text())
    native = OCRExtractor(plan, engine=Engine()).extract_report(
        root / "project-mixed-scan.pdf", document, output_dir=tmp_path
    )
    norm = Normalizer(reviewed_registry).normalize(native)
    assert norm.review_state == "quarantined" and len(norm.blocks) == 4
    with pytest.raises(ValueError, match="pending/quarantined"):
        StructureChunker(CounterTokenizer(), reviewed_registry).build(norm, native)


def test_verified_model_limits_must_all_agree(tmp_path, real_chunk_tokenizer):
    import shutil
    from pathlib import Path

    from dietary_rag.corpus.identity import sha256_bytes

    cache = Path(__file__).resolve().parents[2] / "data/staging/tokenizers/e5-base-v2"
    manifest = dict(real_chunk_tokenizer.metadata)
    manifest.pop("tokenizers_version")
    for name in manifest["files"]:
        shutil.copyfile(cache / name, tmp_path / name)
    model_path = tmp_path / "config.json"
    model = json.loads(model_path.read_text())
    model["max_position_embeddings"] = 600
    model_path.write_text(json.dumps(model))
    manifest["files"] = {**manifest["files"], "config.json": sha256_bytes(model_path.read_bytes())}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="limits disagree"):
        E5Tokenizer(tmp_path, path)


def test_reviewed_project_ocr_fixture_never_enters_consumer_build(tmp_path, reviewed_registry):
    from pathlib import Path

    from dietary_rag.corpus.contracts import DocumentVersion
    from dietary_rag.ingestion.ocr import OCRExtractor
    from dietary_rag.ingestion.ocr_contracts import OCRPlan

    class Engine:
        metadata = {"name": "test", "version": "1", "language": "eng"}

        def recognize(self, path, plan):
            return [
                {
                    "text": "Clean",
                    "conf": "99",
                    "left": "20",
                    "top": "20",
                    "width": "80",
                    "height": "30",
                    "block_num": "1",
                    "par_num": "1",
                    "line_num": "1",
                }
            ]

    root = Path(__file__).resolve().parents[1] / "fixtures/ocr"
    doc = DocumentVersion.model_validate_json((root / "document.json").read_text())
    plan = OCRPlan.model_validate_json((root / "plan.json").read_text())
    native = OCRExtractor(plan, engine=Engine()).extract_report(
        root / "project-mixed-scan.pdf", doc, output_dir=tmp_path
    )
    norm = review_chunk_fixture(Normalizer(reviewed_registry), native)
    assert norm.review_state == "reviewed"
    with pytest.raises(ValueError, match="fixtures"):
        StructureChunker(CounterTokenizer(), reviewed_registry).build(norm, native)
    fixture = StructureChunker(CounterTokenizer(), reviewed_registry, role="fixture").build(
        norm, native
    )
    assert fixture.role == "fixture" and fixture.state == "ready"


def test_oversized_prose_retains_following_paragraph_exception_in_overlap(chunk_evidence):
    normalizer, native, norm = chunk_evidence("mn_cold_storage")
    main = (
        "General advice includes complete supporting details. " * 20
        + "Refrigerate within two hours."
    )
    exception = "If exposed to heat above 90 F, refrigerate within one hour. Further handling advice follows."
    native, norm = synthetic(
        normalizer,
        native,
        [("paragraph", main), ("paragraph", exception), ("paragraph", "New topic context.")],
    )
    build = StructureChunker(
        CounterTokenizer(), normalizer.registry, target_tokens=80, overlap_tokens=40
    ).build(norm, native)
    assert build.state == "ready"
    containing = [c for c in build.chunks if "one hour" in c.text]
    assert containing and all("two hours" in c.text for c in containing)
    for c in build.chunks:
        if any(
            s.overlap and "one hour" in norm.blocks[1].text[s.start : s.end]
            for s in c.spans
            if s.block_id == norm.blocks[1].block_id
        ):
            assert "two hours" in c.text
