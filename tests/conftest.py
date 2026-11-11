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
