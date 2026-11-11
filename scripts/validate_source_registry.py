#!/usr/bin/env python3
"""Validate acquisition metadata; does not fetch sources or validate RAG behavior."""

import argparse
from collections import Counter
from datetime import datetime
import hashlib
from pathlib import Path
import sys
from urllib.parse import urlparse

try:
    import yaml
except ImportError:
    sys.exit("Install PyYAML to validate the source registry.")


def validate(registry, root, check_cache=False):
    errors = []

    def require(condition, message):
        if not condition:
            errors.append(message)

    require(registry.get("schema_version") == 1, "Unsupported registry schema")
    policy = registry["policy"]
    sources = registry["sources"]
    ids = [source["document_id"] for source in sources]
    require(len(ids) == len(set(ids)), "Duplicate document IDs")
    require(set(policy["existing_document_ids"]) <= set(ids), "Existing source IDs were removed")
    require(set(policy["allowed_formats"]) == {"html", "pdf"}, "Both HTML and PDF must be supported")
    defaults = []
    approved_pdfs = []
    verified_pdfs = []
    for source in sources:
        sid = source["document_id"]
        access = source["access"]
        selection = source["selection"]
        parsed = urlparse(source["source_url"])
        require(parsed.scheme == "https" and bool(parsed.hostname), f"{sid}: invalid HTTPS source URL")
        require(source["format"] in policy["allowed_formats"], f"{sid}: invalid expected format")
        try:
            timestamp = datetime.fromisoformat(access["checked_at"])
            require(timestamp.tzinfo is not None, f"{sid}: acquisition time lacks timezone")
        except (ValueError, TypeError):
            errors.append(f"{sid}: invalid acquisition timestamp")
        if access["publicly_accessible"]:
            require(access["status_code"] == 200, f"{sid}: accessible source without HTTP 200")
            require(source["format"] == source["detected_format"], f"{sid}: accessible format mismatch")
            require(len(access.get("sha256") or "") == 64, f"{sid}: missing downloaded content hash")
        if selection["default_index"]:
            defaults.append(source)
            require(selection["decision"] == "approved_with_exclusions", f"{sid}: unapproved default source")
            require(access["publicly_accessible"], f"{sid}: inaccessible default source")
            require(source["target_audience"] == "US", f"{sid}: non-US default source")
            require(source["content_role"] == "consumer_guidance", f"{sid}: reference/regulatory default source")
            require(bool(selection["eligible_sections"]), f"{sid}: default source lacks eligible sections")
        if selection["decision"] in policy["blocked_decisions"]:
            require(not selection["default_index"], f"{sid}: disabled decision selected for default indexing")
        for alternate in source.get("alternate_document_ids", []):
            require(alternate in ids, f"{sid}: unknown alternate ID {alternate}")
        if source.get("pdf"):
            pdf = source["pdf"]
            verified_pdfs.append(source)
            require(source["format"] == source["detected_format"] == "pdf", f"{sid}: verified PDF format mismatch")
            require(access["publicly_accessible"], f"{sid}: verified PDF inaccessible")
            require(source.get("direct_pdf_url") == source["resolved_url"], f"{sid}: missing verified direct PDF URL")
            require(pdf["page_count"] > 0, f"{sid}: invalid PDF page count")
            require(0 <= pdf["pages_with_extractable_text"] <= pdf["page_count"], f"{sid}: invalid text-page count")
            require(bool(pdf["text_classification"] and pdf["layout_notes"]), f"{sid}: missing PDF inspection")
            require(all(1 <= page <= pdf["page_count"] for page in pdf["sampled_pages"]), f"{sid}: invalid sampled page")
            if selection.get("approved_pdf_for_milestone"):
                approved_pdfs.append(source)
                require(selection["decision"] == "approved_with_exclusions", f"{sid}: PDF approval conflicts with decision")
                require(source["source_tier"] == "official_guidance" and source["jurisdiction"].startswith("US"), f"{sid}: milestone PDF not official US source")
            if check_cache:
                path = root / "data/staging/source_review" / registry["review_date"] / f"{sid}.pdf"
                if not path.exists():
                    errors.append(f"{sid}: missing local PDF cache")
                else:
                    content = path.read_bytes()
                    require(content.startswith(b"%PDF-"), f"{sid}: cached file lacks PDF signature")
                    require(hashlib.sha256(content).hexdigest() == access["sha256"], f"{sid}: cached hash differs")
                    try:
                        from pypdf import PdfReader
                        require(len(PdfReader(path).pages) == pdf["page_count"], f"{sid}: actual page count differs")
                    except ImportError:
                        errors.append("Install pypdf for --check-cache parser/page-count validation")
                        break
                    except Exception as exc:
                        errors.append(f"{sid}: PDF parser failed: {exc}")
        elif selection.get("approved_pdf_for_milestone"):
            errors.append(f"{sid}: milestone approval without verified PDF metadata")

    require(len(approved_pdfs) >= policy["minimum_verified_official_us_pdfs"], "Fewer than five approved official US PDFs")
    families = [source["family_id"] for source in defaults]
    require(len(families) == len(set(families)), "Multiple default assets from one guidance family")
    actual = {
        "candidate_url_count": len(sources),
        "preserved_existing_source_count": len(policy["existing_document_ids"]),
        "verified_official_us_pdf_count": len(verified_pdfs),
        "approved_official_us_pdf_count": len(approved_pdfs),
        "default_selected_asset_count": len(defaults),
        "default_selected_unique_family_count": len(set(families)),
        "decision_counts": dict(Counter(source["selection"]["decision"] for source in sources)),
    }
    for key, value in actual.items():
        require(registry["review_summary"].get(key) == value, f"Stale summary: {key}")
    return errors, actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=Path(__file__).resolve().parents[1] / "config/sources.yaml")
    parser.add_argument("--check-cache", action="store_true", help="Check downloaded PDF hashes, signatures and actual page counts (requires pypdf)")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        registry = yaml.safe_load(args.registry.read_text())
        errors, counts = validate(registry, root, args.check_cache)
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        sys.exit(f"Invalid registry: {exc}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Registry valid: {counts['candidate_url_count']} candidates; {counts['preserved_existing_source_count']} existing IDs retained; {counts['approved_official_us_pdf_count']} approved / {counts['verified_official_us_pdf_count']} verified official PDFs; {counts['default_selected_unique_family_count']} default families.")
    if args.check_cache:
        print("All verified PDF cache signatures, SHA-256 hashes and physical page counts match.")
    print("Acquisition metadata checks passed; extraction quality and chatbot release gates remain separate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
