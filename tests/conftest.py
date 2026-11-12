from pathlib import Path

import pytest

from dietary_rag.corpus.registry import load_registry


@pytest.fixture(scope="session")
def reviewed_registry():
    return load_registry(Path(__file__).resolve().parents[1] / "config/sources.yaml")


def _pdf_bytes(*, text=True, image=False, encrypted=False):
    from io import BytesIO

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    resources = DictionaryObject()
    if text:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        resources[NameObject("/Font")] = DictionaryObject(
            {NameObject("/F1"): writer._add_object(font)}
        )
        content = DecodedStreamObject()
        content.set_data(
            b"BT /F1 12 Tf 10 200 Td (Synthetic fixture: keep food refrigerated and wash your hands.) Tj ET"
        )
        page[NameObject("/Contents")] = writer._add_object(content)
    if image:
        obj = DecodedStreamObject()
        obj[NameObject("/Subtype")] = NameObject("/Image")
        obj.set_data(b"synthetic image marker")
        resources[NameObject("/XObject")] = DictionaryObject(
            {NameObject("/Im0"): writer._add_object(obj)}
        )
    page[NameObject("/Resources")] = resources
    if encrypted:
        writer.encrypt("secret")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture
def pdf_factory():
    return _pdf_bytes


def _chunk_native_fixture(registry, name, monkeypatch):
    from dietary_rag.corpus.contracts import DocumentVersion, SourceEligibility
    from dietary_rag.corpus.identity import sha256_bytes
    from dietary_rag.ingestion.html import HTMLExtractor
    from dietary_rag.ingestion.normalization import Normalizer
    from dietary_rag.ingestion.pdf import PDFExtractor
    from dietary_rag.ingestion.pdf_profiles import REVIEWED_SOURCES

    FIXTURES = Path(__file__).resolve().parent / "fixtures"
    source = registry.get(name)
    kind = source.format
    path = FIXTURES / kind / f"{name}.{kind}"
    content = path.read_bytes()
    source = source.model_copy(
        update={
            "access": source.access.model_copy(
                update={"sha256": sha256_bytes(content), "bytes": len(content)}
            )
        }
    )
    registry = registry.model_copy(
        update={"sources": tuple(source if s.document_id == name else s for s in registry.sources)}
    )
    if kind == "pdf":
        monkeypatch.setitem(
            REVIEWED_SOURCES, name, {**REVIEWED_SOURCES[name], "raw_sha256": source.access.sha256}
        )
    fields = {
        "document_id",
        "family_id",
        "document_name",
        "publisher",
        "source_url",
        "resolved_url",
        "format",
        "dates",
        "source_tier",
        "jurisdiction",
        "target_audience",
        "intended_audience",
        "content_role",
        "rights_review",
        "author",
        "medical_reviewer",
    }
    document = DocumentVersion(
        **source.model_dump(include=fields),
        document_version_id="docv_normalization_fixture",
        retrieved_at=source.access.checked_at,
        raw_sha256=source.access.sha256,
        eligibility=SourceEligibility.model_validate(
            source.selection.model_dump(exclude={"approved_pdf_for_milestone"})
        ),
        **(
            {
                "page_count": source.pdf.page_count,
                "text_classification": source.pdf.text_classification,
            }
            if kind == "pdf"
            else {}
        ),
    )
    parser = (
        HTMLExtractor(registry, name)
        if kind == "html"
        else PDFExtractor(registry, name, comparison=not source.selection.default_index)
    )
    return Normalizer(registry), parser.extract_report(path, document)


@pytest.fixture
def chunk_evidence(reviewed_registry, monkeypatch):
    """Source-derived portable fixtures with exact, explicitly labeled test review."""

    def prepare(name):
        normalizer, native = _chunk_native_fixture(reviewed_registry, name, monkeypatch)
        return normalizer, native, review_chunk_fixture(normalizer, native)

    return prepare


def review_chunk_fixture(normalizer, native):
    from dietary_rag.ingestion.normalization_contracts import NormalizationReview

    pending = normalizer.normalize(native)
    receipt = NormalizationReview(
        normalization_id=pending.normalization_id,
        configuration_id=pending.configuration_id,
        original_extraction_id=native.extraction_id,
        raw_sha256=native.document.raw_sha256,
        block_ids=tuple(b.block_id for b in pending.blocks),
        reviewer="Project-authored chunk fixture review",
        scope="Exact transformation/exclusion fidelity, fixture only",
        note="Portable fixture review; no activation.",
    )
    return normalizer.normalize(native, review=receipt)


@pytest.fixture(scope="session")
def real_chunk_tokenizer():
    from dietary_rag.ingestion.chunk_tokenizer import E5Tokenizer

    root = Path(__file__).resolve().parents[1]
    cache = root / "data/staging/tokenizers/e5-base-v2"
    if not (cache / "tokenizer.json").exists():
        pytest.skip(
            "Pinned real tokenizer integration: run prepare-tokenizer first; no test downloads"
        )
    return E5Tokenizer(cache, root / "config/tokenizer-e5-base-v2.json")
