from __future__ import annotations

from pathlib import Path

from docx import Document as WordDocument
from reyvo.integrations.knowledge.docx_parser import (
    chunk_text,
    heading_level,
    parse_docx,
    slugify_anchor,
)


def _build_docx(tmp_path: Path) -> Path:
    document = WordDocument()
    document.core_properties.title = "Reyvo Test Design"
    document.add_heading("1. Introduction", level=1)
    document.add_paragraph("Purpose of the platform.")
    document.add_heading("1.1 Scope", level=2)
    document.add_paragraph("Tenant isolation uses PostgreSQL row level security.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Control"
    table.cell(0, 1).text = "Default"
    table.cell(1, 0).text = "Daily cap"
    table.cell(1, 1).text = "50 per inbox"
    path = tmp_path / "design.docx"
    document.save(path)
    return path


def test_parse_docx_keeps_heading_breadcrumbs_and_tables(tmp_path):
    parsed = parse_docx(_build_docx(tmp_path))

    assert parsed.title == "Reyvo Test Design"
    assert parsed.block_count > 0
    assert any("1. Introduction > 1.1 Scope" in section.heading_path for section in parsed.sections)
    assert any("row level security" in section.text for section in parsed.sections)
    assert any("Daily cap | 50 per inbox" in section.text for section in parsed.sections)


def test_parse_docx_assigns_unique_anchors(tmp_path):
    parsed = parse_docx(_build_docx(tmp_path))
    anchors = [section.anchor for section in parsed.sections]

    assert len(anchors) == len(set(anchors))
    assert "1-1-scope" in anchors


def test_slugify_anchor_normalizes_headings():
    assert slugify_anchor("4.3 Technology stack (proposed)") == "4-3-technology-stack-proposed"
    assert slugify_anchor("  Overview  ") == "overview"
    assert slugify_anchor("???") == "section"


def test_slugify_anchor_truncates_pathological_headings():
    slug = slugify_anchor("word " * 500)

    assert len(slug) <= 120
    assert slug == slug.strip("-")


def test_parse_docx_truncates_heading_paths(tmp_path):
    document = WordDocument()
    for level in range(1, 10):
        document.add_heading(f"Level {level} " + "detail " * 100, level=level)
    document.add_paragraph("Deeply nested body text.")
    path = tmp_path / "deep.docx"
    document.save(path)

    parsed = parse_docx(path)

    assert parsed.sections
    assert all(len(section.heading_path) <= 500 for section in parsed.sections)


def test_parse_docx_dedupes_anchors_across_chunk_splits(tmp_path):
    document = WordDocument()
    # "Overview" splits into multiple chunks (overview-1, overview-2, ...).
    document.add_heading("Overview", level=1)
    document.add_paragraph("first " * 400)
    # A second section that also slugs to "overview" would naturally collide
    # with the chunk anchors above.
    document.add_heading("Overview", level=1)
    document.add_paragraph("second section")
    path = tmp_path / "collisions.docx"
    document.save(path)

    parsed = parse_docx(path)
    anchors = [section.anchor for section in parsed.sections]

    assert len(anchors) > 2
    assert len(anchors) == len(set(anchors))


def test_heading_level_understands_word_styles():
    assert heading_level("Heading 1") == 1
    assert heading_level("heading3") == 3
    assert heading_level("Title") == 1
    assert heading_level("Normal") is None
    assert heading_level("Table") is None


def test_chunk_text_respects_target_size():
    paragraphs = [f"Paragraph {index}: " + ("word " * 20) for index in range(12)]
    chunks = chunk_text("\n\n".join(paragraphs), target_chars=200, overlap_chars=50)

    assert len(chunks) > 1
    assert all(len(chunk) <= 200 for chunk in chunks)


def test_chunk_text_overlaps_adjacent_chunks():
    paragraphs = [f"P{index}: " + ("detail " * 3) for index in range(12)]
    chunks = chunk_text("\n\n".join(paragraphs), target_chars=100, overlap_chars=40)

    assert len(chunks) > 1
    for previous, following in zip(chunks, chunks[1:], strict=False):
        assert any(part in following for part in previous.split("\n\n"))


def test_chunk_text_splits_oversized_paragraphs():
    chunks = chunk_text("x" * 1_000, target_chars=200, overlap_chars=40)

    assert len(chunks) >= 5
    assert all(len(chunk) <= 200 for chunk in chunks)


def test_chunk_text_returns_empty_for_blank_input():
    assert chunk_text("   \n\n  ") == []
