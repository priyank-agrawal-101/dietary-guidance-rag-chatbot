"""Deterministic, reviewed-profile HTML extraction; no network or corpus activation."""

import re
from dataclasses import asdict
from pathlib import Path
from sys import version_info

from bs4 import BeautifulSoup, NavigableString, Tag
from bs4 import __version__ as bs4_version

from dietary_rag.corpus.contracts import ContentBlock, DocumentVersion, SourceLocation, TableContext
from dietary_rag.corpus.identity import sha256_bytes, stable_id
from dietary_rag.corpus.registry import SourceRegistry
from dietary_rag.ingestion.html_contracts import (
    HTMLCell,
    HTMLDiagnostic,
    HTMLExtraction,
    HTMLTable,
    HTMLTableRow,
)
from dietary_rag.ingestion.html_profiles import PROFILES, REVIEWED_SELECTIONS

PARSER_VERSION = "html-native-1"
FURNITURE = (
    "nav, aside, footer, script, style, noscript, template, iframe, form, button, "
    "img, svg, video, audio, .on-this-page, .dfe-curated-link, .page-mobile-bar, "
    '.page-content-sources, .related-content, .advertisement, [role="navigation"]'
)


class HTMLExtractionError(ValueError):
    """Unsupported or ambiguous content cannot become evidence."""


def _text(node: Tag, *, own: bool = False) -> str:
    parts = []

    def visit(item):
        if isinstance(item, NavigableString):
            parts.append(str(item))
        elif isinstance(item, Tag):
            if own and item is not node and item.name in ("ul", "ol", "table"):
                return
            if item.name in ("br", "p", "li", "div"):
                parts.append(" ")
            for child in item.children:
                visit(child)
            if item.name in ("p", "li", "div"):
                parts.append(" ")

    visit(node)
    value = re.sub(r"\s+", " ", "".join(parts)).strip()
    return re.sub(r"\s+([,.;:!?])", r"\1", value)


def _path(node: Tag) -> str:
    parts = []
    for item in [node, *node.parents]:
        if item.name == "[document]":
            break
        siblings = [s for s in item.previous_siblings if isinstance(s, Tag) and s.name == item.name]
        parts.append(f"{item.name}:nth-of-type({len(siblings) + 1})")
    return " > ".join(reversed(parts))


def _matches(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def _list_text(node: Tag, depth: int = 0) -> str:
    lines = []
    for index, li in enumerate(node.find_all("li", recursive=False), 1):
        prefix = f"{index}." if node.name == "ol" else "-"
        own = _text(li, own=True)
        if own:
            lines.append(f"{'  ' * depth}{prefix} {own}")
        for nested in li.find_all(["ul", "ol"], recursive=False):
            lines.append(_list_text(nested, depth + 1))
    return "\n".join(line for line in lines if line)


def _table_grid(table: Tag, paths: dict[int, str]):
    if table.find("table"):
        raise HTMLExtractionError("nested tables require a separate reviewed layout")
    trs = table.find_all("tr")
    if not trs or len(trs) > 1000:
        raise HTMLExtractionError("table row count is empty or exceeds 1000")
    cells, occupied = [], {}
    for row, tr in enumerate(trs):
        column = 0
        for cell in tr.find_all(["td", "th"], recursive=False):
            while (row, column) in occupied:
                column += 1
            try:
                rs, cs = int(cell.get("rowspan", 1)), int(cell.get("colspan", 1))
            except (TypeError, ValueError) as exc:
                raise HTMLExtractionError("invalid table span") from exc
            if not (1 <= rs <= len(trs) - row and 1 <= cs <= 100 and column + cs <= 100):
                raise HTMLExtractionError("table span exceeds bounds or uses unsupported rowspan=0")
            index = len(cells)
            cells.append(
                HTMLCell(
                    row=row,
                    column=column,
                    rowspan=rs,
                    colspan=cs,
                    is_header=cell.name == "th",
                    scope=cell.get("scope"),
                    text=_text(cell),
                    dom_path=paths[id(cell)],
                )
            )
            for r in range(row, row + rs):
                for c in range(column, column + cs):
                    if (r, c) in occupied:
                        raise HTMLExtractionError("overlapping table spans")
                    occupied[r, c] = index
            column += cs
    width = max((c for _, c in occupied), default=-1) + 1
    if not width or any((r, c) not in occupied for r in range(len(trs)) for c in range(width)):
        raise HTMLExtractionError("ragged table: missing cells cannot be inferred")
    grid = tuple(tuple(occupied[r, c] for c in range(width)) for r in range(len(trs)))
    return tuple(cells), grid


def _logical_rows(cells, grid, *, legacy_td_headers=False):
    width = len(grid[0])
    headers = [""] * width
    rows, category = [], None
    in_header = True
    for r, indexes in enumerate(grid):
        row = [cells[i] for i in indexes]
        values = tuple(c.text for c in row)
        unique = list(dict.fromkeys(indexes))
        nonempty = [cells[i] for i in unique if cells[i].text]
        # Full-width category headings and MDH category bands with empty numeric cells.
        group = (len(unique) == 1 and row[0].colspan == width) or (
            len(nonempty) == 1 and nonempty[0].colspan > 1 and not all(values)
        )
        if group:
            category = nonempty[0].text if nonempty else None
            continue
        is_columns = all(c.is_header and c.scope not in ("row", "rowgroup") for c in row)
        if is_columns or (in_header and legacy_td_headers and r == 0):
            for c, value in enumerate(values):
                # Repeated column headings in CDC category bands reset identically.
                if value and value not in headers[c].split(" / "):
                    headers[c] = " / ".join(filter(None, (headers[c], value)))
            continue
        in_header = False
        if row[0].colspan > 1 and row[0].text and any(c.text for c in row[row[0].colspan :]):
            category = None
        if not all(headers):
            raise HTMLExtractionError("table lacks complete reviewed column headers")
        rows.append(
            HTMLTableRow(
                source_row=r,
                category=category,
                column_headers=tuple(headers),
                values=values,
            )
        )
    if not rows:
        raise HTMLExtractionError("table has no data rows")
    return tuple(rows)


class HTMLExtractor:
    def __init__(self, registry: SourceRegistry, document_id: str):
        decision = registry.consumer_eligibility(document_id)
        if not decision.eligible_for_consumer_ingestion:
            raise HTMLExtractionError(
                "source is not approved consumer guidance: " + ", ".join(decision.reasons)
            )
        self.source = registry.get(document_id)
        if document_id not in PROFILES or self.source.format != "html":
            raise HTMLExtractionError("no reviewed HTML profile for this source")
        self.profile = PROFILES[document_id]
        if self.source.parser_profile != self.profile.registry_profile:
            raise HTMLExtractionError("registry parser profile changed; review required")
        review = stable_id("mask_review", {"selection": self.source.selection})
        if review != REVIEWED_SELECTIONS[document_id]:
            raise HTMLExtractionError(
                "registry section selection changed; review the extraction mask"
            )
        self.configuration_id = stable_id(
            "html_config",
            {
                "profile": asdict(self.profile),
                "selection": review,
                "parser": PARSER_VERSION,
                "furniture": FURNITURE,
                "beautifulsoup_version": bs4_version,
                "python_html_parser": f"{version_info.major}.{version_info.minor}",
            },
        )

    def extract(self, raw_path: Path, document: DocumentVersion) -> tuple[ContentBlock, ...]:
        """Shared Extractor protocol; use extract_report for diagnostics/native structures."""
        return self.extract_report(raw_path, document).blocks

    def extract_report(self, raw_path: Path, document: DocumentVersion) -> HTMLExtraction:
        content = raw_path.read_bytes()
        if (
            sha256_bytes(content) != document.raw_sha256
            or document.raw_sha256 != self.source.access.sha256
        ):
            raise HTMLExtractionError("raw HTML differs from the acquired/reviewed SHA-256")
        # Match all reviewed attribution/eligibility fields, not merely a URL or document ID.
        fields = (
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
        )
        if any(getattr(document, f) != getattr(self.source, f) for f in fields) or (
            document.eligibility.model_dump()
            != self.source.selection.model_dump(exclude={"approved_pdf_for_milestone"})
        ):
            raise HTMLExtractionError("document metadata differs from the reviewed registry")
        soup = BeautifulSoup(content, "html.parser")
        roots = soup.select(self.profile.root)
        if len(roots) != 1:
            raise HTMLExtractionError(f"expected one main-content root, found {len(roots)}")
        root = roots[0]
        paths = {id(n): _path(n) for n in soup.find_all(True)}
        originals = {id(t): str(t) for t in root.find_all("table")}
        diagnostics = []

        def omit(node, code, detail):
            diagnostics.append(
                HTMLDiagnostic(code=code, dom_path=paths.get(id(node)), detail=detail)
            )
            node.decompose()

        for node in list(root.select(FURNITURE)):
            if node.parent is not None:
                omit(node, "furniture_removed", node.name)
        for node in list(root.find_all(True)):
            if node.parent is None:
                continue
            style = re.sub(r"\s+", "", node.get("style", "")).lower()
            if (
                node.has_attr("hidden")
                or node.get("aria-hidden") == "true"
                or "display:none" in style
                or "visibility:hidden" in style
            ):
                omit(node, "hidden_removed", "explicitly hidden DOM content")
        # Only deduplicate explicit responsive presentations, never repeated recommendations.
        seen = {}
        for node in list(
            root.select("[data-responsive-copy], .d-none, .d-md-none, .hidden-xs, .visible-xs")
        ):
            if node.parent is None:
                continue
            key = _text(node)
            if key and key in seen:
                omit(
                    node,
                    "responsive_duplicate_removed",
                    "identical text in marked responsive copies",
                )
            elif key:
                seen[key] = node
        # Remove excluded list-item wording while retaining legitimate nested recommendations.
        for li in list(root.find_all("li")):
            if li.parent is None or not _matches(self.profile.excluded_text, _text(li, own=True)):
                continue
            diagnostics.append(
                HTMLDiagnostic(
                    code="excluded_text", dom_path=paths[id(li)], detail=_text(li, own=True)
                )
            )
            nested = li.find_all(["ul", "ol"], recursive=False)
            if nested and self.source.document_id == "mn_safe_temperatures":
                for child in list(li.contents):
                    if child not in nested:
                        child.extract()
            else:
                li.decompose()
        blocks, tables, headings = [], [], []
        heading_anchors = {}
        seen_sections = set()
        seen_duplicate_text = set()
        recent_prose = None

        def location(node):
            anchor = node.get("id") or node.get("name")
            if not anchor:
                anchor = next(
                    (
                        heading_anchors.get(level)
                        for level, _ in reversed(headings)
                        if heading_anchors.get(level)
                    ),
                    None,
                )
            if not anchor:
                for parent in node.parents:
                    if parent is soup:
                        break
                    if parent.get("id"):
                        anchor = parent.get("id")
                        break
            if not anchor:
                anchor = next(
                    (
                        heading_anchors.get(level)
                        for level, _ in reversed(headings)
                        if heading_anchors.get(level)
                    ),
                    None,
                )
            return SourceLocation(
                format="html",
                extraction_method="html",
                source_anchor=anchor,
                dom_path=paths[id(node)],
            )

        def emit(node, kind, value, context=None):
            if not value:
                return None
            payload = dict(
                document_version_id=document.document_version_id,
                block_type=kind,
                heading_path=tuple(t for _, t in headings),
                sequence_index=len(blocks),
                text=value,
                source_locations=(location(node),),
                table_context=context,
            )
            block = ContentBlock(
                block_id=stable_id("block", {"config": self.configuration_id, **payload}), **payload
            )
            blocks.append(block)
            return block

        def allowed():
            top = next((t for level, t in headings if level == 2), None)
            return (not self.profile.sections or top in self.profile.sections) and not any(
                title in self.profile.excluded_sections for _, title in headings
            )

        def walk(node):
            nonlocal recent_prose
            if not isinstance(node, Tag):
                return
            if re.fullmatch(r"h[1-6]", node.name):
                level, title = int(node.name[1]), _text(node)
                if set(node.get("class", ())) & {"cdc-callout__heading", "cdc-actions__heading"}:
                    if allowed():
                        emit(node, "note", title)
                    return
                if not title:
                    return
                headings[:] = [
                    (prior_level, t) for prior_level, t in headings if prior_level < level
                ] + [(level, title)]
                heading_anchors[level] = node.get("id")
                recent_prose = None
                if level == 2:
                    seen_sections.add(title)
                if level == 1 or allowed():
                    emit(node, "heading", title)
                else:
                    diagnostics.append(
                        HTMLDiagnostic(
                            code="excluded_section", dom_path=paths[id(node)], detail=title
                        )
                    )
                return
            if node.name in ("p", "ul", "ol", "table"):
                if not allowed():
                    return
                value = _text(node)
                if node.name == "p" and (
                    value.startswith("On this page:")
                    or (
                        node.find("a")
                        and all(a.get("href", "").startswith("#") for a in node.find_all("a"))
                        and not any(
                            str(s).strip(" |\n\t")
                            for s in node.find_all(string=True)
                            if s.find_parent("a") is None
                        )
                    )
                ):
                    diagnostics.append(
                        HTMLDiagnostic(
                            code="navigation_removed", dom_path=paths[id(node)], detail=value
                        )
                    )
                    return
                if (
                    self.source.document_id == "fda_seafood"
                    and node.name == "p"
                    and value.startswith(
                        "Most seafood should be cooked to an internal temperature of 145°F."
                    )
                ):
                    diagnostics.append(
                        HTMLDiagnostic(
                            code="excluded_text",
                            dom_path=paths[id(node)],
                            detail=value[
                                len(
                                    "Most seafood should be cooked to an internal temperature of 145°F."
                                ) :
                            ].strip(),
                        )
                    )
                    value = "Most seafood should be cooked to an internal temperature of 145°F."
                if value in self.profile.duplicate_text:
                    if value in seen_duplicate_text:
                        diagnostics.append(
                            HTMLDiagnostic(
                                code="source_duplicate_removed",
                                dom_path=paths[id(node)],
                                detail=value,
                            )
                        )
                        return
                    seen_duplicate_text.add(value)
                if _matches(self.profile.excluded_text, value):
                    diagnostics.append(
                        HTMLDiagnostic(code="excluded_text", dom_path=paths[id(node)], detail=value)
                    )
                    return
                if self.profile.retained_text:
                    if node.name in ("ul", "ol"):
                        for li in node.find_all("li", recursive=False):
                            if _matches(self.profile.retained_text, _text(li)):
                                emit(li, "recommendation_list", "- " + _text(li))
                        return
                    if node.name == "table" or not _matches(self.profile.retained_text, value):
                        if value:
                            diagnostics.append(
                                HTMLDiagnostic(
                                    code="outside_text_allowlist",
                                    dom_path=paths[id(node)],
                                    detail=value[:180],
                                )
                            )
                        return
                if node.name == "table":
                    # Known MDH one-row presentation tables carry only a source/date tagline.
                    if self.source.document_id.startswith("mn_") and len(node.find_all("tr")) == 1:
                        emit(node, "note", value)
                        diagnostics.append(
                            HTMLDiagnostic(
                                code="layout_table",
                                dom_path=paths[id(node)],
                                detail="one-row metadata presentation, not a guidance matrix",
                            )
                        )
                        return
                    cells, grid = _table_grid(node, paths)
                    rows = _logical_rows(
                        cells, grid, legacy_td_headers=self.profile.legacy_td_headers
                    )
                    notes = []
                    for footer in node.find_all("tfoot"):
                        if _text(footer):
                            notes.append(_text(footer))
                    for tr in node.find_all("tr"):
                        cell = tr.find(["td", "th"], recursive=False)
                        if (
                            cell
                            and int(cell.get("colspan", 1)) == len(grid[0])
                            and re.match(r"^(note|\*|†|footnote)", _text(cell), re.I)
                        ):
                            notes.append(_text(cell))
                    if recent_prose:
                        notes.append(recent_prose)
                    for linked in node.select('a[href^="#"]'):
                        target = soup.find(id=linked["href"][1:])
                        if target is None or root not in target.parents:
                            raise HTMLExtractionError(
                                "unresolved or out-of-content table note reference"
                            )
                        if target not in node.descendants:
                            notes.append(_text(target))
                    for sibling in node.next_siblings:
                        if not isinstance(sibling, Tag):
                            continue
                        if sibling.name != "p" and not set(sibling.get("class", ())) & {
                            "footnotes",
                            "footnote",
                        }:
                            break
                        notes.append(_text(sibling))
                    title = (
                        _text(node.caption)
                        if node.caption
                        else (headings[-1][1] if headings else document.document_name)
                    )
                    headers = rows[0].column_headers
                    units = tuple(
                        dict.fromkeys(
                            re.findall(
                                r"\([^)]*(?:°|days|months|hours)[^)]*\)", " ".join(headers), re.I
                            )
                        )
                    )
                    context = TableContext(
                        title=title,
                        column_headers=headers,
                        row_headers=tuple(
                            dict.fromkeys(
                                r.category or r.values[0] for r in rows if r.category or r.values[0]
                            )
                        ),
                        units=units,
                        footnotes=tuple(dict.fromkeys(n for n in notes if n)),
                    )
                    lines = [title]
                    for row in rows:
                        lines.append(
                            ("Category: " + row.category + " | " if row.category else "")
                            + " | ".join(
                                f"{h}: {v}"
                                for h, v in zip(row.column_headers, row.values, strict=True)
                            )
                        )
                    lines.extend(context.footnotes)
                    block = emit(node, "table", "\n".join(lines), context)
                    tables.append(
                        HTMLTable(
                            block_id=block.block_id,
                            original_html=originals[id(node)],
                            cells=cells,
                            grid=grid,
                            rows=rows,
                        )
                    )
                    return
                if node.name in ("ul", "ol"):
                    emit(node, "recommendation_list", _list_text(node))
                else:
                    kind = (
                        "note" if re.match(r"^(note|\*|†|footnote)", value, re.I) else "paragraph"
                    )
                    emit(node, kind, value)
                    recent_prose = value or recent_prose
                return
            if node.name in ("div", "blockquote", "section", "dd") and not node.find(
                ["p", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6", "div"]
            ):
                value = _text(node)
                if (
                    value
                    and allowed()
                    and not self.profile.retained_text
                    and not _matches(self.profile.excluded_text, value)
                ):
                    emit(
                        node,
                        "note" if "cdc-callout__body" in node.get("class", ()) else "paragraph",
                        value,
                    )
                return
            for child in list(node.children):
                walk(child)

        if not root.find("h1"):
            h1 = soup.select_one("main h1")
            if h1 is None:
                raise HTMLExtractionError("missing source title")
            walk(h1)
        walk(root)
        missing = set(self.profile.sections) - seen_sections
        if missing:
            raise HTMLExtractionError("reviewed sections missing: " + ", ".join(sorted(missing)))
        if len(tables) != self.profile.expected_tables:
            raise HTMLExtractionError(
                f"expected {self.profile.expected_tables} guidance tables, found {len(tables)}"
            )
        if not any(b.block_type != "heading" for b in blocks):
            raise HTMLExtractionError("main content contains no retained guidance")
        diagnostics.append(
            HTMLDiagnostic(
                code="review_pending",
                detail="Native extraction only; automated checks do not establish semantic approval. Visual-only content omitted; do not infer image guidance or fetch linked pages.",
            )
        )
        payload = dict(
            parser_version=PARSER_VERSION,
            profile=self.source.parser_profile,
            configuration_id=self.configuration_id,
            document=document,
            blocks=tuple(blocks),
            tables=tuple(tables),
            diagnostics=tuple(diagnostics),
        )
        return HTMLExtraction(extraction_id=stable_id("html_extraction", payload), **payload)
