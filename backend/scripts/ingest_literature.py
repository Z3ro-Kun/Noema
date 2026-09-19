"""Ingest one plain-text public-domain literary work.

    python -m scripts.ingest_literature --file ../data/raw/alice.txt \
        --title "Alice's Adventures in Wonderland" --author "Lewis Carroll" \
        --source-ref 11 --source-url https://www.gutenberg.org/ebooks/11

Pass --url to download the text first if the file isn't there yet. The
downloaded corpus lands under data/ which is gitignored: we record where the
text came from, we don't redistribute it.
"""

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

from app.core.db import async_session_factory, engine
from app.services.ingestion.literature import MalformedSourceError, PlainTextLiteratureAdapter
from app.services.ingestion.service import UnknownDomainError, ingest_source_work


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, type=Path, help="path to the plain-text source")
    parser.add_argument("--title", required=True)
    parser.add_argument("--source-ref", required=True, help="stable id within the source")
    parser.add_argument("--author", default=None)
    parser.add_argument("--source-name", default="gutenberg")
    parser.add_argument("--source-url", default=None, help="recorded as provenance")
    parser.add_argument("--url", default=None, help="download to --file first if missing")
    parser.add_argument(
        "--license-note",
        default=None,
        help="recorded as provenance, e.g. the work's public-domain status",
    )
    return parser.parse_args(argv)


def resolve_source_text(path: Path, url: str | None) -> str:
    if not path.exists():
        if url is None:
            raise FileNotFoundError(f"{path} does not exist (pass --url to download it)")
        path.parent.mkdir(parents=True, exist_ok=True)
        response = httpx.get(url, timeout=60.0, follow_redirects=True)
        response.raise_for_status()
        # newline="" disables newline translation on write. Without it, a
        # source that already uses CRLF gets each "\n" expanded to "\r\n",
        # producing "\r\r\n"; reading that back with universal newlines
        # yields *two* line breaks, so every line becomes its own paragraph
        # and the work's structure silently falls apart.
        path.write_text(response.text, encoding="utf-8", newline="")
        print(f"downloaded {len(response.text):,} chars -> {path}")
    return path.read_text(encoding="utf-8")


async def run(args: argparse.Namespace) -> int:
    try:
        text = resolve_source_text(args.file, args.url)
    except (FileNotFoundError, httpx.HTTPError) as exc:
        print(f"source unavailable: {exc}", file=sys.stderr)
        return 1

    adapter = PlainTextLiteratureAdapter(
        text=text,
        title=args.title,
        source_ref=args.source_ref,
        source_name=args.source_name,
        author=args.author,
        source_url=args.source_url,
        source_file=args.file.name,
        license_note=args.license_note,
    )

    try:
        source_work = adapter.load()
    except MalformedSourceError as exc:
        print(f"could not parse source: {exc}", file=sys.stderr)
        return 1

    print(
        f"parsed '{source_work.title}': {len(source_work.containers)} containers, "
        f"{source_work.content_unit_count} content units"
    )

    try:
        async with async_session_factory() as session:
            try:
                result = await ingest_source_work(session, source_work)
            except UnknownDomainError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            await session.commit()
    finally:
        # Close pooled connections before the loop does, or Windows' proactor
        # loop reports "Event loop is closed" while tearing them down.
        await engine.dispose()

    if result.created:
        print(
            f"ingested work {result.work_id}: "
            f"{result.containers} containers, {result.content_units} content units"
        )
    else:
        print(f"already ingested as work {result.work_id}; nothing to do")
    return 0


def main() -> int:
    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
