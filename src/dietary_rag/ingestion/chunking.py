"""Structure-first chunks with atomic qualifications and verified token budgets."""

import re
from dataclasses import dataclass

from dietary_rag.corpus.identity import sha256_bytes, stable_id
from dietary_rag.ingestion.chunk_contracts import Chunk, ChunkBuild, ChunkRows, ChunkSpan
from dietary_rag.ingestion.normalization import Normalizer, parse_extraction
from dietary_rag.ingestion.normalization_contracts import NormalizedExtraction
from dietary_rag.ingestion.pdf_profiles import PROFILES

VERSION = "structure_chunker_v3"
DEPENDENT = re.compile(
    r"^(?:Reduce that|However\b|But\b|If\b|Unless\b|Except\b|This\b|These\b|That\b|Only\b|Otherwise\b|In hot weather\b|The exception\b)",
    re.I,
)


class ChunkingError(ValueError):
    pass


@dataclass(frozen=True)
class Unit:
    block_id: str
    start: int
    end: int
    text: str


def sentence_ranges(text):
    boundaries = []
    for match in re.finditer(r"(?<=[.!?])\s+(?=[A-Z0-9])", text):
        preceding = text[: match.start()]
        if re.search(r"(?:U\.S\.|U\.S\.A\.|Dr\.|Mr\.|Ms\.|e\.g\.|i\.e\.)$", preceding):
            continue
        boundaries.append(match.end())
    starts = [0, *boundaries]
    ends = [*boundaries, len(text)]
    ranges = []
    for start, end in zip(starts, ends, strict=True):
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start == end:
            continue
        if ranges and DEPENDENT.match(text[start:end]):
            ranges[-1] = (ranges[-1][0], end)
        else:
            ranges.append((start, end))
    return ranges


def list_ranges(text):
    # Indented children stay attached to their top-level recommendation.
    starts = [m.start() for m in re.finditer(r"(?m)^(?:[-•+▪●‣]|\d+[.)])\s+", text)]
    if not starts:
        return [(0, len(text))]
    if starts[0] > 0:
        starts[0] = 0  # Preserve any original list introduction with its first item.
    return [
        (a, (b if i + 1 < len(starts) else len(text)))
        for i, (a, b) in enumerate(zip(starts, starts[1:] + [len(text)], strict=True))
    ]


def render_rows(table, indices):
    c = table.context
    lines = [f"Table: {c.title}"]
    for index in indices:
        row = table.rows[index]
        if row.category:
            lines.append(f"Category: {row.category}")
        lines.extend(
            f"{header}: {value if value else '[blank in source]'}"
            for header, value in zip(row.column_headers, row.values, strict=True)
        )
        lines.append("")
    lines.extend(f"Note: {note}" for note in c.footnotes)
    return "\n".join(lines).strip()


def embedding_text(document, heading, body):
    context = [f"Document: {document.document_name}"]
    short = [h for h in heading if h != document.document_name and len(h.split()) <= 12]
    if short:
        context.append(f"Section: {short[-1]}")
    context.append(f"Audience: {document.intended_audience}")
    return "passage: " + "\n".join(context) + "\n\n" + body


class StructureChunker:
    def __init__(
        self, tokenizer, registry, *, target_tokens=350, overlap_tokens=50, role="consumer"
    ):
        if not 0 < target_tokens <= tokenizer.limit or not 0 <= overlap_tokens < target_tokens:
            raise ChunkingError("chunk budget exceeds verified model limit or invalid overlap")
        if role not in ("consumer", "comparison", "fixture"):
            raise ChunkingError("unknown chunk role")
        self.tokenizer, self.registry = tokenizer, registry
        self.target, self.overlap, self.role = target_tokens, overlap_tokens, role
        self.configuration = stable_id(
            "chunk_config",
            {
                "version": VERSION,
                "tokenizer": tokenizer.metadata,
                "target": target_tokens,
                "overlap": overlap_tokens,
                "role": role,
                "blank_marker": "[blank in source]",
                "context": "document-last-short-section-audience",
                "conditions": DEPENDENT.pattern,
            },
        )

    def build(self, normalized, native):
        normalized = (
            NormalizedExtraction.model_validate_json(normalized.model_dump_json())
            if isinstance(normalized, NormalizedExtraction)
            else NormalizedExtraction.model_validate(normalized)
        )
        native = parse_extraction(native)
        if normalized.review_state != "reviewed":
            raise ChunkingError(
                "chunking requires an exact reviewed normalization; pending/quarantined evidence unavailable"
            )
        expected = Normalizer(self.registry).normalize(
            native, artifact_sha256=normalized.original_artifact_sha256, review=normalized.review
        )
        if expected != normalized:
            raise ChunkingError(
                "normalized evidence differs from original artifact and reviewed transformations"
            )
        synthetic = bool(normalized.fixture_kind and normalized.fixture_kind != "original_pdf")
        if self.role == "consumer" and (
            normalized.comparison_only
            or synthetic
            or not self.registry.consumer_eligibility(
                normalized.document.document_id
            ).eligible_for_consumer_ingestion
        ):
            raise ChunkingError(
                "consumer chunks cannot include comparisons, fixtures or ineligible evidence"
            )
        if self.role == "comparison" and (not normalized.comparison_only or synthetic):
            raise ChunkingError("comparison build requires explicitly reviewed comparison evidence")
        if self.role == "fixture" and not synthetic:
            raise ChunkingError("fixture build requires labeled project-created evidence")
        blocks = {b.block_id: b for b in normalized.blocks}
        tables = {t.block_id: t for t in normalized.tables}
        originals = {
            d.normalized_block_id: d.original_block_id
            for d in normalized.decisions
            if d.state == "retained"
        }
        layouts = {layout.block_id: layout for layout in getattr(native, "layouts", ())}
        chunks = []
        diagnostics = []
        document = normalized.document

        def region(block):
            if block.block_type == "table":
                return ("table",)
            if normalized.format_adapter == "native_pdf":
                layout = layouts[originals[block.block_id]]
                parts = []
                for page in dict.fromkeys(loc.physical_page for loc in block.source_locations):
                    reg = next(
                        (
                            r
                            for r in PROFILES[document.document_id].pages[page - 1].regions
                            if r.key == layout.region_key
                        ),
                        None,
                    )
                    parts.append(
                        f"continuation:{reg.continuation_key}"
                        if reg and reg.continuation_key
                        else f"page:{page}:{layout.region_key}"
                    )
                return tuple(dict.fromkeys(parts))
            if normalized.format_adapter == "ocr":
                original_id = originals[block.block_id]
                candidate = next(c for c in native.candidates if c.block_id == original_id)
                return (candidate.image_id,)
            return ("html-section",)

        def size(body, heading):
            return self.tokenizer.count(embedding_text(document, heading, body))

        def emit(units, kind, heading, regions, overlap_count=0, table=None, indices=()):
            body = (
                render_rows(table, indices)
                if table
                else "\n\n".join(u.text.rstrip() for u in units)
            )
            count = size(body, heading)
            if count > self.tokenizer.limit:
                diagnostics.append(
                    f"Atomic {kind} evidence ({table.block_id if table else units[0].block_id}, rows {tuple(indices)}) exceeds {self.tokenizer.limit} tokens; quarantined without truncation."
                )
                return
            spans = (
                ()
                if table
                else tuple(
                    ChunkSpan(
                        block_id=u.block_id, start=u.start, end=u.end, overlap=i < overlap_count
                    )
                    for i, u in enumerate(units)
                )
            )
            contributing = (
                (table.block_id,) if table else tuple(dict.fromkeys(u.block_id for u in units))
            )
            locations = []
            pages = {table.rows[i].physical_page for i in indices} if table else set()
            for bid in contributing:
                for loc in blocks[bid].source_locations:
                    if table and pages != {None} and loc.physical_page not in pages:
                        continue
                    if loc not in locations:
                        locations.append(loc)
            payload = dict(
                document_id=document.document_id,
                document_version_id=document.document_version_id,
                family_id=document.family_id,
                sequence_index=len(chunks),
                chunk_type=kind,
                heading_path=heading,
                region_keys=regions,
                text=body,
                embedding_text=embedding_text(document, heading, body),
                token_count=count,
                text_sha256=sha256_bytes(body.encode()),
                spans=spans,
                table_rows=ChunkRows(
                    block_id=table.block_id,
                    original_table_id=table.original_table_id,
                    row_indices=tuple(indices),
                )
                if table
                else None,
                table_context=table.context if table else None,
                source_locations=tuple(locations),
            )
            chunks.append(
                Chunk(
                    chunk_id=stable_id(
                        "chunk",
                        {
                            "config": self.configuration,
                            "normalization": normalized.normalization_id,
                            **payload,
                        },
                    ),
                    **payload,
                )
            )

        def pack(units, kind, heading, regions, allow_overlap=False, introductions=()):
            # Dependent conditions form one atomic group, even across paragraphs.
            atoms = []
            for unit in units:
                if kind == "prose" and atoms and DEPENDENT.match(unit.text):
                    atoms[-1].append(unit)
                else:
                    atoms.append([unit])
            context = [[u] for u in introductions]
            current = list(context)
            overlapping = 0

            def flatten(groups):
                return [u for group in groups for u in group]

            def body(groups):
                return "\n\n".join(u.text.rstrip() for u in flatten(groups))

            for atom in atoms:
                proposed = current + [atom]
                if len(current) > len(context) and size(body(proposed), heading) > self.target:
                    emit(flatten(current), kind, heading, regions, overlapping)
                    retained = []
                    if allow_overlap:
                        for old in reversed(current):
                            if (
                                self.tokenizer.count(body([old, *retained]), special_tokens=False)
                                > self.overlap
                            ):
                                break
                            retained.insert(0, old)
                        while retained and size(body([*retained, atom]), heading) > self.target:
                            retained.pop(0)
                    current = list(context) + retained
                    overlapping = len(flatten(retained))
                current.append(atom)
            if len(current) > len(context):
                emit(flatten(current), kind, heading, regions, overlapping)

        pending = []
        pending_key = None

        def flush():
            nonlocal pending, pending_key
            if pending:
                pack(pending, "prose", pending_key[0], pending_key[1])
                pending = []
            pending_key = None

        skipped_blocks = set()
        for block_index, block in enumerate(normalized.blocks):
            if block.block_id in skipped_blocks:
                continue
            if block.block_type == "heading":
                flush()
                continue
            heading = block.heading_path
            regions = region(block)
            key = (heading, regions)
            if block.block_type == "table":
                flush()
                table = tables[block.block_id]
                all_indices = list(range(len(table.rows)))
                small = size(render_rows(table, all_indices), heading) <= self.target
                if small and document.document_id != "cdc_pregnancy_food_safety":
                    emit([], "table", heading, regions, table=table, indices=all_indices)
                    continue
                current = []
                category = None
                for index, row in enumerate(table.rows):
                    label = row.category or (
                        next((v for v in row.values if v), None)
                        if document.document_id in ("mn_storage_chart", "mn_storage_chart_pdf")
                        else None
                    )
                    separate = document.document_id == "cdc_pregnancy_food_safety"
                    if current and (
                        separate
                        or label != category
                        or size(render_rows(table, [*current, index]), heading) > self.target
                    ):
                        emit([], "table", heading, regions, table=table, indices=current)
                        current = []
                    current.append(index)
                    category = label
                    if separate:
                        emit([], "table", heading, regions, table=table, indices=current)
                        current = []
                if current:
                    emit([], "table", heading, regions, table=table, indices=current)
                continue
            if block.block_type == "recommendation_list":
                introductions = []
                if pending and pending_key == key and pending[-1].text.rstrip().endswith(":"):
                    introductions.append(pending.pop())
                flush()
                members = [block]
                # Native HTML may expose sibling <li> elements as separate blocks.
                # Join only the same original list container, never independent cards.
                loc = block.source_locations[0]
                parent = (
                    loc.dom_path.rsplit(" > ", 1)[0]
                    if (
                        loc.format == "html"
                        and re.search(r" > li:nth-of-type\(\d+\)$", loc.dom_path or "")
                    )
                    else None
                )
                if parent:
                    for following in normalized.blocks[block_index + 1 :]:
                        floc = following.source_locations[0]
                        if (
                            following.block_type != "recommendation_list"
                            or following.heading_path != heading
                            or region(following) != regions
                            or floc.format != "html"
                            or floc.dom_path.rsplit(" > ", 1)[0] != parent
                        ):
                            break
                        members.append(following)
                        skipped_blocks.add(following.block_id)
                whole = introductions + [Unit(b.block_id, 0, len(b.text), b.text) for b in members]
                if size("\n\n".join(u.text for u in whole), heading) <= self.target:
                    emit(
                        whole,
                        "list",
                        heading,
                        regions,
                    )
                else:
                    if len(members) > 1:
                        units = [
                            Unit(b.block_id, a, end, b.text[a:end].rstrip())
                            for b in members
                            for a, end in list_ranges(b.text)
                        ]
                        pack(units, "list", heading, regions, introductions=introductions)
                        continue
                    starts = [
                        m.start() for m in re.finditer(r"(?m)^(?:[-•+▪●‣]|\d+[.)])\s+", block.text)
                    ]
                    if starts and starts[0] > 0:
                        introductions.append(
                            Unit(block.block_id, 0, starts[0], block.text[: starts[0]].rstrip())
                        )
                    units = [
                        Unit(block.block_id, a, b, block.text[a:b].rstrip())
                        for a, b in list_ranges(block.text)
                    ]
                    if starts and starts[0] > 0:
                        units[0] = Unit(
                            block.block_id,
                            starts[0],
                            units[0].end,
                            block.text[starts[0] : units[0].end].rstrip(),
                        )
                    pack(units, "list", heading, regions, introductions=introductions)
                continue
            if size(block.text, heading) > self.target:
                flush()
                units = [
                    Unit(block.block_id, a, b, block.text[a:b])
                    for a, b in sentence_ranges(block.text)
                ]
                for following in normalized.blocks[block_index + 1 :]:
                    if (
                        following.block_type not in ("paragraph", "note")
                        or following.heading_path != heading
                        or region(following) != regions
                        or not DEPENDENT.match(following.text)
                    ):
                        break
                    units.extend(
                        Unit(following.block_id, start, end, following.text[start:end])
                        for start, end in sentence_ranges(following.text)
                    )
                    skipped_blocks.add(following.block_id)
                pack(units, "prose", heading, regions, allow_overlap=True)
                continue
            if pending_key is not None and pending_key != key:
                flush()
            pending_key = key
            pending.append(Unit(block.block_id, 0, len(block.text), block.text))
        flush()
        payload = dict(
            chunker_version=VERSION,
            configuration_id=self.configuration,
            normalization_id=normalized.normalization_id,
            original_extraction_id=native.extraction_id,
            document=document,
            role=self.role,
            tokenizer=self.tokenizer.metadata,
            target_tokens=self.target,
            overlap_tokens=self.overlap,
            input_limit=self.tokenizer.limit,
            chunks=tuple(chunks),
            diagnostics=tuple(diagnostics),
            state="quarantined" if diagnostics else "ready",
        )
        result = ChunkBuild(build_id=stable_id("chunk_build", payload), **payload)
        validate_chunk_links(result, normalized, self.tokenizer)
        return result


def validate_chunk_links(build, normalized, tokenizer):
    if (
        build.normalization_id != normalized.normalization_id
        or build.original_extraction_id != normalized.original_extraction_id
        or build.document != normalized.document
        or build.tokenizer != tokenizer.metadata
        or build.input_limit != tokenizer.limit
    ):
        raise ChunkingError("build differs from canonical evidence or verified tokenizer")
    blocks = {b.block_id: b for b in normalized.blocks}
    tables = {t.block_id: t for t in normalized.tables}
    covered = {
        b.block_id: []
        for b in normalized.blocks
        if b.block_type != "heading" and b.block_type != "table"
    }
    rows = {bid: [] for bid in tables}
    for chunk in build.chunks:
        if embedding_text(build.document, chunk.heading_path, chunk.text) != chunk.embedding_text:
            raise ChunkingError("embedding context differs from original document/section/audience")
        if tokenizer.count(chunk.embedding_text) != chunk.token_count:
            raise ChunkingError("actual tokenizer count differs from chunk")
        if chunk.chunk_type == "table":
            ref = chunk.table_rows
            table = tables[ref.block_id]
            if (
                ref.original_table_id != table.original_table_id
                or any(i >= len(table.rows) for i in ref.row_indices)
                or render_rows(table, ref.row_indices) != chunk.text
                or chunk.table_context != table.context
            ):
                raise ChunkingError("table chunk differs from original rows, headers or qualifiers")
            rows[ref.block_id].extend(ref.row_indices)
            contributing = [blocks[ref.block_id]]
            pages = {table.rows[i].physical_page for i in ref.row_indices}
        else:
            text = "\n\n".join(
                blocks[s.block_id].text[s.start : s.end].rstrip() for s in chunk.spans
            )
            if text != chunk.text:
                raise ChunkingError("chunk body differs from exact original block slices")
            for span in chunk.spans:
                if span.end > len(blocks[span.block_id].text):
                    raise ChunkingError("chunk span exceeds original block")
                covered[span.block_id].append((span.start, span.end))
            contributing = [blocks[bid] for bid in dict.fromkeys(s.block_id for s in chunk.spans)]
            pages = {None}
        expected_locations = []
        for block in contributing:
            if block.heading_path != chunk.heading_path:
                raise ChunkingError("chunk crosses original section boundary")
            for loc in block.source_locations:
                if pages != {None} and loc.physical_page not in pages:
                    continue
                if loc not in expected_locations:
                    expected_locations.append(loc)
        if tuple(expected_locations) != chunk.source_locations:
            raise ChunkingError("chunk geometry differs from contributing original locations")
    if build.state == "ready":
        for bid, intervals in covered.items():
            text = blocks[bid].text
            marked = [False] * len(text)
            for start, end in intervals:
                marked[start:end] = [True] * (end - start)
            if any(not marked[i] and not char.isspace() for i, char in enumerate(text)):
                raise ChunkingError("chunk build omitted meaningful original text")
        if any(sorted(rows[bid]) != list(range(len(t.rows))) for bid, t in tables.items()):
            raise ChunkingError("chunk build omitted or duplicated table rows")
