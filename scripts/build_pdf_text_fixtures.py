"""Rebuild portable text-only PDF fixtures from explicit native-extraction CLI reports.

These are reconstructed coordinate fixtures, never original acquisition files.
The optional pdf-dev extra supplies ReportLab; images and logos are not copied.
"""

import argparse
import json
from pathlib import Path

from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from dietary_rag.corpus.identity import sha256_bytes
from dietary_rag.ingestion.pdf_contracts import PDFExtraction


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    extractions = {}
    for report_path in args.reports:
        report = json.loads(report_path.read_text())
        for result in report["results"]:
            if result["status"] != "extracted_pending_review":
                raise ValueError("fixture inputs require successful pending-review extraction")
            extraction = PDFExtraction.model_validate_json(Path(result["output"]).read_text())
            name = extraction.document.document_id
            if name in extractions:
                raise ValueError("choose exactly one reviewed extraction per source")
            extractions[name] = extraction
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, data in sorted(extractions.items()):
        target = args.output_dir / (name + ".pdf")
        writer = canvas.Canvas(str(target), invariant=1, pageCompression=1)
        for page in data.pages:
            writer.setPageSize((page.width, page.height))
            for word in data.words:
                if word.location.physical_page != page.physical_page:
                    continue
                x0, top, x1, bottom = word.location.bbox
                font = (
                    "Helvetica-Bold"
                    if any(k in word.font_name.lower() for k in ("bold", "demi"))
                    else "Helvetica"
                )
                size = bottom - top
                text = word.text.replace("▪", "•")
                obj = writer.beginText(x0, page.height - top - 0.793 * size)
                obj.setFont(font, size)
                measured = stringWidth(text, font, size)
                if measured:
                    obj.setHorizScale((x1 - x0) / measured * 100)
                # Explicit spaces preserve token boundaries when reconstructed fonts differ.
                obj.textOut(text + " ")
                writer.drawText(obj)
            writer.showPage()
        writer.save()
        manifest.append(
            {
                "document_id": name,
                "fixture_sha256": sha256_bytes(target.read_bytes()),
                "original_sha256": data.document.raw_sha256,
                "source_url": str(data.document.source_url),
                "description": "Source-derived native-text coordinate fixture; rebuilt with Helvetica, changed square bullets to round bullets, omitted all images/logos/vector art. Not original acquisition bytes.",
                "expected_tables": [
                    [
                        {"values": list(row.values), "category": row.category, "state": row.state}
                        for row in table.parts[0].rows
                    ]
                    for table in data.tables
                ],
            }
        )
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
