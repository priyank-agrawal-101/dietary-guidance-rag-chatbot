"""File-level checks only; guidance extraction/chunking remains Phase 2."""

import re
from html.parser import HTMLParser
from io import BytesIO
from typing import Literal

from dietary_rag.corpus.contracts import Contract, PositiveInt, SourceFormat


class InspectionError(ValueError):
    def __init__(self, code: str, message: str, detected_format: SourceFormat | None = None):
        super().__init__(message)
        self.code = code
        self.detected_format = detected_format


class FileInspection(Contract):
    detected_format: SourceFormat
    page_count: PositiveInt | None = None
    text_classification: (
        Literal[
            "selectable_text", "mostly_selectable_with_sparse_pages", "scanned", "mixed", "unknown"
        ]
        | None
    ) = None
    warnings: tuple[str, ...] = ()


class _HTMLSignals(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.in_title = False
        self.in_main = 0
        self.text = []
        self.main_text = []
        self.title = []
        self.pdf_links = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag == "title":
            self.in_title = True
        if tag in ("main", "article"):
            self.in_main += 1
        if tag == "a" and any(
            key == "href" and ".pdf" in (value or "").lower() for key, value in attrs
        ):
            self.pdf_links += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        if tag == "title":
            self.in_title = False
        if tag in ("main", "article"):
            self.in_main = max(0, self.in_main - 1)

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        if not self.hidden and not self.in_title:
            self.text.append(data)
            if self.in_main:
                self.main_text.append(data)


def inspect_file(
    content: bytes, expected: SourceFormat, content_type: str | None = None
) -> FileInspection:
    if not content:
        raise InspectionError("empty_document", "The response/file is empty")
    prefix = content[:8192].lstrip().lower()
    detected = (
        "pdf"
        if content.startswith(b"%PDF-")
        else (
            "html"
            if not prefix.startswith((b"{", b"["))
            and re.search(rb"<(?:!doctype\s+html|html|head|body|main|article)\b", prefix)
            else None
        )
    )
    if detected is None:
        raise InspectionError(
            "unsupported_content", "No supported HTML/PDF file structure was found"
        )
    if detected != expected:
        raise InspectionError(
            "format_mismatch", "Actual file format differs from the registered format", detected
        )
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    warnings = ()
    if (detected == "pdf" and mime.startswith("text/")) or (
        detected == "html" and mime == "application/pdf"
    ):
        warnings = ("content_type_disagrees_with_actual_format",)
    if detected == "html":
        charset = re.search(r"charset=[\"']?([\w-]+)", content_type or "", re.I)
        try:
            text = content.decode(charset.group(1) if charset else "utf-8", errors="replace")
        except LookupError as exc:
            raise InspectionError(
                "invalid_encoding", "Unrecognized HTML character encoding", "html"
            ) from exc
        signals = _HTMLSignals()
        signals.feed(text)
        title = " ".join(signals.title).strip().lower()
        if (
            any(
                word in title
                for word in (
                    "access denied",
                    "just a moment",
                    "sign in",
                    "log in",
                    "login",
                    "captcha",
                )
            )
            or "cf-chl-" in text.lower()
        ):
            raise InspectionError(
                "access_challenge", "The HTML is an access/login/challenge page", "html"
            )
        visible = " ".join(" ".join(signals.text).split())
        main = " ".join(" ".join(signals.main_text).split())
        if len(visible) < 80:
            raise InspectionError(
                "application_shell", "The HTML lacks substantial readable content", "html"
            )
        if (
            signals.pdf_links
            and len(main or visible) < 160
            and "download" in (main or visible).lower()
        ):
            raise InspectionError(
                "download_only_page", "A download summary is not the guidance document", "html"
            )
        return FileInspection(detected_format="html", warnings=warnings)
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("Install requirements/acquisition.lock to inspect PDFs") from exc
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise InspectionError(
                "encrypted_document", "Password/encrypted PDFs require separate review", "pdf"
            )
        count = len(reader.pages)
        if not 1 <= count <= 2000:
            raise InspectionError(
                "invalid_page_count",
                "PDF page count is empty or exceeds the inspection budget",
                "pdf",
            )
        native = 0
        sparse_images = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            if len(text.strip()) >= 40:
                native += 1
            else:
                resources = page.get("/Resources", {})
                resources = (
                    resources.get_object() if hasattr(resources, "get_object") else resources
                )
                objects = resources.get("/XObject", {})
                objects = objects.get_object() if hasattr(objects, "get_object") else objects
                sparse_images += any(
                    obj.get_object().get("/Subtype") == "/Image" for obj in objects.values()
                )
        classification = (
            "selectable_text"
            if native == count
            else (
                ("scanned" if sparse_images == count else "unknown")
                if native == 0
                else ("mixed" if sparse_images else "mostly_selectable_with_sparse_pages")
            )
        )
        return FileInspection(
            detected_format="pdf",
            page_count=count,
            text_classification=classification,
            warnings=warnings,
        )
    except InspectionError:
        raise
    except Exception as exc:
        raise InspectionError(
            "corrupt_pdf", "The PDF could not be opened and inspected", "pdf"
        ) from exc
