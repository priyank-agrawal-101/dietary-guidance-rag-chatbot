"""Original-page and geometry-overlay PNG exports for administrative PDF review."""

from pathlib import Path

from dietary_rag.corpus.identity import sha256_bytes
from dietary_rag.ingestion.pdf_contracts import PDFExtraction


def render_pdf_review(raw_path: Path, extraction: PDFExtraction, output_dir: Path, *, dpi=110):
    import pypdfium2
    from PIL import ImageDraw

    if not 48 <= dpi <= 200:
        raise ValueError("review rendering DPI must be between 48 and 200")
    if sha256_bytes(raw_path.read_bytes()) != extraction.document.raw_sha256:
        raise ValueError("render input differs from the extracted original PDF")
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    scale = dpi / 72
    with pypdfium2.PdfDocument(raw_path) as pdf:
        for index, page_map in enumerate(extraction.pages):
            if page_map.width * page_map.height * scale * scale > 20_000_000:
                raise ValueError("review page exceeds 20-million-pixel rendering bound")
            page = pdf[index]
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil().convert("RGB")
            prefix = f"{extraction.document.document_id}-page-{index + 1:03d}"
            original = output_dir / (prefix + ".png")
            overlay = output_dir / (prefix + "-overlay.png")
            image.save(original)
            drawing = ImageDraw.Draw(image)
            for block in extraction.blocks:
                for loc in block.source_locations:
                    if loc.physical_page == index + 1 and loc.bbox:
                        box = tuple(v * scale for v in loc.bbox)
                        drawing.rectangle(box, outline="#c62828", width=2)
                        drawing.text(
                            (box[0], max(0, box[1] - 12)), str(block.sequence_index), fill="#c62828"
                        )
            for table in extraction.tables:
                for part in table.parts:
                    if part.physical_page != index + 1:
                        continue
                    excluded = {r.source_row for r in part.rows if r.state != "retained"}
                    for cell in part.cells:
                        drawing.rectangle(
                            tuple(v * scale for v in cell.location.bbox),
                            outline="#ef6c00" if cell.row in excluded else "#00897b",
                            width=1,
                        )
            image.save(overlay)
            results.append(
                {
                    "physical_page": index + 1,
                    "printed_page_label": page_map.printed_page_label,
                    "original": str(original),
                    "overlay": str(overlay),
                    "original_png_sha256": sha256_bytes(original.read_bytes()),
                    "overlay_png_sha256": sha256_bytes(overlay.read_bytes()),
                    "dpi": dpi,
                }
            )
            image.close()
            bitmap.close()
            page.close()
    return results
