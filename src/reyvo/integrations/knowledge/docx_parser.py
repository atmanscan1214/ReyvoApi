"""Heading-aware parsing and chunking for Word documents.

The design document is the source of truth for the assistant, so ingestion keeps
its structure: heading breadcrumbs, paragraph boundaries and tables. Chunks are
built per section and never split a heading away from the text that follows it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

DEFAULT_CHUNK_CHARS = 1_200
DEFAULT_OVERLAP_CHARS = 200
# Changing chunking/parsing behaviour must bump PARSER_VERSION in
# ``reyvo.services.knowledge_service``; otherwise an unchanged file checksum
# skips re-ingestion and the old chunks stay live.
MAX_ANCHOR_CHARS = 120
MAX_HEADING_PATH_CHARS = 500

_HEADING_RE = re.compile(r"^heading\s*(\d+)$", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(slots=True)
class ParsedSection:
    heading_path: str
    anchor: str
    text: str


@dataclass(slots=True)
class ParsedDocument:
    title: str
    version: str | None
    sections: list[ParsedSection] = field(default_factory=list)
    block_count: int = 0


@dataclass(slots=True)
class _Block:
    style: str
    text: str


def slugify_anchor(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    slug = slug[:MAX_ANCHOR_CHARS].strip("-")
    return slug or "section"


def heading_level(style: str) -> int | None:
    name = (style or "").strip()
    if name.lower() == "title":
        return 1
    match = _HEADING_RE.match(name)
    return int(match.group(1)) if match else None


def render_table(table: Table) -> str:
    rows: list[str] = []
    for row in table.rows:
        cells: list[str] = []
        seen: set[int] = set()
        for cell in row.cells:
            # Merged cells are reported once per grid column; keep only the
            # first occurrence so retrieved context is not duplicated.
            identity = id(cell._tc)
            if identity in seen:
                continue
            seen.add(identity)
            cells.append(_WHITESPACE_RE.sub(" ", cell.text).strip())
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _iter_blocks(document: Document) -> Iterator[_Block]:
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            style = paragraph.style.name if paragraph.style is not None else ""
            if not text or style.lower().startswith("toc"):
                continue
            yield _Block(style=style, text=text)
        elif child.tag == qn("w:tbl"):
            text = render_table(Table(child, document))
            if text:
                yield _Block(style="Table", text=text)


def _first_title(blocks: list[_Block], fallback: str) -> str:
    for block in blocks:
        if block.style.strip().lower() == "title" and block.text.strip():
            return block.text.strip()
    for block in blocks:
        if block.text.strip():
            return block.text.strip()
    return fallback


def build_sections(blocks: list[_Block], *, title: str) -> list[ParsedSection]:
    headings: dict[int, str] = {}
    sections: list[ParsedSection] = []
    buffer: list[str] = []
    seen_anchors: dict[str, int] = {}

    def path() -> str:
        if not headings:
            return "Overview"
        joined = " > ".join(headings[level] for level in sorted(headings))
        return joined[:MAX_HEADING_PATH_CHARS]

    def anchor_for() -> str:
        base = slugify_anchor(headings[max(headings)] if headings else "overview")
        seen_anchors[base] = seen_anchors.get(base, 0) + 1
        count = seen_anchors[base]
        return base if count == 1 else f"{base}-{count}"

    def flush() -> None:
        text = "\n\n".join(part for part in buffer if part.strip()).strip()
        if not text:
            buffer.clear()
            return
        sections.append(ParsedSection(heading_path=path(), anchor=anchor_for(), text=text))
        buffer.clear()

    for block in blocks:
        level = heading_level(block.style)
        if level is None:
            buffer.append(block.text)
            continue
        # A heading closes the previous section and updates the breadcrumb.
        if buffer:
            flush()
        for existing in [key for key in headings if key >= level]:
            del headings[existing]
        headings[level] = block.text.strip()

    if buffer:
        flush()

    if not sections:
        sections.append(ParsedSection(heading_path="Overview", anchor="overview", text=title))
    return sections


def _split_long_line(line: str, target: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", line)
    units: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > target:
            if current:
                units.append(current)
                current = ""
            units.extend(
                sentence[index : index + target] for index in range(0, len(sentence), target)
            )
        elif current and len(current) + 1 + len(sentence) > target:
            units.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        units.append(current)
    return units


def _split_units(text: str, target: int) -> list[str]:
    units: list[str] = []
    for block in re.split(r"\n{2,}", text.strip()):
        block = block.strip()
        if not block:
            continue
        if len(block) <= target:
            units.append(block)
            continue
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            if len(line) <= target:
                units.append(line)
            else:
                units.extend(_split_long_line(line, target))
    return units


def chunk_text(
    text: str,
    *,
    target_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[str]:
    units = _split_units(text, target_chars)
    chunks: list[str] = []
    current: list[str] = []
    size = 0

    for unit in units:
        if current and size + len(unit) + 2 > target_chars:
            chunks.append("\n\n".join(current))
            carry: list[str] = []
            carry_size = 0
            for previous in reversed(current):
                added = len(previous) + 2
                if carry and carry_size + added > overlap_chars:
                    break
                if carry_size + added + len(unit) + 2 > target_chars:
                    break
                carry.insert(0, previous)
                carry_size += added
            current = carry
            size = carry_size
        current.append(unit)
        size += len(unit) + 2

    if current:
        chunks.append("\n\n".join(current))

    return [chunk.strip() for chunk in chunks if chunk.strip()]


def parse_docx(
    path: str | Path,
    *,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> ParsedDocument:
    document = Document(str(path))
    blocks = list(_iter_blocks(document))
    fallback = Path(path).stem
    title = (document.core_properties.title or "").strip() or _first_title(blocks, fallback)
    version = (document.core_properties.version or "").strip() or None

    sections: list[ParsedSection] = []
    seen_anchors: set[str] = set()

    def unique_anchor(candidate: str) -> str:
        anchor = candidate
        suffix = 2
        while anchor in seen_anchors:
            anchor = f"{candidate}-{suffix}"
            suffix += 1
        seen_anchors.add(anchor)
        return anchor

    for section in build_sections(blocks, title=title):
        pieces = chunk_text(section.text, target_chars=chunk_chars, overlap_chars=overlap_chars)
        for index, chunk in enumerate(pieces):
            anchor = section.anchor if len(pieces) == 1 else f"{section.anchor}-{index + 1}"
            sections.append(
                ParsedSection(
                    heading_path=section.heading_path,
                    anchor=unique_anchor(anchor),
                    text=chunk,
                )
            )

    return ParsedDocument(
        title=title,
        version=version,
        sections=sections,
        block_count=len(blocks),
    )
