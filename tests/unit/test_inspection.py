import pytest

from dietary_rag.ingestion.inspection import InspectionError, inspect_file


@pytest.mark.parametrize(
    "content,expected,code",
    [
        (b"", "html", "empty_document"),
        (b'{"html": "not a page"}', "html", "unsupported_content"),
        (b"<html><body>" + b"food safety " * 20 + b"</body></html>", "pdf", "format_mismatch"),
        (b"%PDF-1.7\nbroken", "pdf", "corrupt_pdf"),
        (b"<html><title>Access Denied</title><body>" + b"denied " * 40, "html", "access_challenge"),
        (
            b"<html><body><div id='app'></div><script>"
            + b"appcode" * 100
            + b"</script></body></html>",
            "html",
            "application_shell",
        ),
        (
            b"<html><body><main><a href='food.pdf'>Download this official dietary guidance PDF document for complete nutrition recommendations and related educational material.</a></main></body></html>",
            "html",
            "download_only_page",
        ),
    ],
)
def test_reject_invalid_documents(content, expected, code):
    with pytest.raises(InspectionError) as exc:
        inspect_file(content, expected)
    assert exc.value.code == code


def test_actual_structure_is_used_not_mime_or_filename(pdf_factory):
    result = inspect_file(pdf_factory(), "pdf", "text/html")
    assert result.page_count == 1
    assert result.text_classification == "selectable_text"
    assert result.warnings == ("content_type_disagrees_with_actual_format",)
    assert (
        inspect_file(
            b"<article>" + b"Store food safely. " * 20 + b"</article>", "html"
        ).detected_format
        == "html"
    )


def test_scan_and_blank_are_distinguished_and_encryption_is_rejected(pdf_factory):
    assert inspect_file(pdf_factory(text=False, image=True), "pdf").text_classification == "scanned"
    assert inspect_file(pdf_factory(text=False), "pdf").text_classification == "unknown"
    with pytest.raises(InspectionError) as exc:
        inspect_file(pdf_factory(encrypted=True), "pdf")
    assert exc.value.code == "encrypted_document"
