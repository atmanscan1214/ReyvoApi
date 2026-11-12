"""Ingest a Word document into the database knowledge base.

Run from ``src``:

    uv run python scripts\\ingest_kb.py

By default this ingests ``DESIGN_DOC_PATH`` (the Reyvo detailed design document).
Re-running is a no-op unless the file changed; pass ``--force`` to rebuild chunks.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from reyvo.core.config import get_settings
from reyvo.db.session import (
    build_engine,
    build_session_factory,
    dispose_engine,
    init_models,
)
from reyvo.services.knowledge_service import (
    DESIGN_DOC_SLUG,
    DESIGN_DOC_TITLE,
    ingest_document,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", help="Path to the .docx file (defaults to DESIGN_DOC_PATH)")
    parser.add_argument("--slug", default=DESIGN_DOC_SLUG, help="Stable knowledge document slug")
    parser.add_argument("--title", default=DESIGN_DOC_TITLE, help="Human-readable document title")
    parser.add_argument("--version", default=None, help="Optional document version label")
    parser.add_argument(
        "--force", action="store_true", help="Re-chunk even if the file is unchanged"
    )
    parser.add_argument(
        "--create-tables",
        action="store_true",
        help="Create missing tables before ingesting (for local SQLite runs)",
    )
    return parser.parse_args()


async def ingest(args: argparse.Namespace) -> int:
    settings = get_settings()
    source = Path(args.path) if args.path else Path(settings.design_doc_path)
    if not source.is_absolute():
        source = Path.cwd() / source
    if not source.exists():
        print(f"Document not found: {source}")
        return 1

    engine = build_engine(settings)
    if settings.auto_create_tables or args.create_tables:
        await init_models(engine)
    session_factory = build_session_factory(engine)
    try:
        async with session_factory() as session:
            result = await ingest_document(
                session,
                path=source,
                slug=args.slug,
                title=args.title,
                version=args.version,
                force=args.force,
            )
    finally:
        await dispose_engine(engine)

    if result.skipped:
        print(f"Knowledge base already up to date: {result.slug} ({result.chunk_count} chunks)")
        return 0

    print(f"Ingested {result.title} ({result.slug})")
    print(f"  source:   {source}")
    print(f"  chunks:   {result.chunk_count}")
    print(f"  embedded: {result.embedded_chunk_count}")
    print(f"  checksum: {result.checksum[:12]}…")
    return 0


def main() -> int:
    args = parse_args()
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(ingest(args))
    return asyncio.run(ingest(args))


if __name__ == "__main__":
    raise SystemExit(main())
