"""Bring the corpus up to what the manifest describes.

    python -m scripts.seed_corpus --dry-run
    python -m scripts.seed_corpus --domain anime
    python -m scripts.seed_corpus

Phase 1AC. Reads `scripts.corpus_manifest` and ingests whatever is missing,
using the same adapters and the same `ingest_source_work` the one-off scripts
have always used. Nothing here parses a source, invents a field or writes a
row by hand: this is a loop over the manifest and a call into the existing
ingestion service.

Idempotent by construction. `ingest_source_work` keys on `(source,
source_ref)`, so a work already in the corpus is reported and skipped rather
than duplicated -- which is what makes it safe to run against a database that
already holds seventeen works and real user libraries hanging off them.

Each work is committed on its own. A single transaction over twenty books is
one interruption away from losing all of them, and the whole point of an
idempotent seeder is that it can be run again and pick up where it stopped.

Literature texts are downloaded to `data/raw/` on first use and read from
there afterwards. That directory is gitignored: Noema records where a text
came from, it does not redistribute it.

What this does *not* do:

    concepts     `scripts.populate_work_concepts`
    embeddings   `scripts.embed_corpus`
    covers       `scripts.enrich_covers`

Three separate passes, because each is slow in its own way and each is
already a script that knows how to skip work it has done. Run them after
this one; `scripts.validate_corpus` checks the result.
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

import httpx

from app.core.db import async_session_factory, engine
from app.services.ingestion.anilist_client import (
    AniListClient,
    AniListError,
    AniListNotFoundError,
)
from app.services.ingestion.anime import AniListAnimeAdapter
from app.services.ingestion.literature import MalformedSourceError, PlainTextLiteratureAdapter
from app.services.ingestion.manga import AniListMangaAdapter
from app.services.ingestion.service import (
    UnknownDomainError,
    ingest_source_work,
    resolve_source_relations,
)
from scripts.corpus_manifest import PLAN, AniListEntry, LiteratureEntry

RAW = Path(__file__).resolve().parents[2] / "data" / "raw"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", default=None, help="literature | anime | manhwa")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between source fetches")
    parser.add_argument("--dry-run", action="store_true", help="report without committing")
    return parser.parse_args(argv)


def literature_text(entry: LiteratureEntry) -> str:
    """The plain text, downloaded once and cached under data/raw."""
    path = RAW / f"gutenberg-{entry.ebook_id}.txt"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        response = httpx.get(entry.text_url, timeout=120.0, follow_redirects=True)
        response.raise_for_status()
        # newline="" disables newline translation: a source that already uses
        # CRLF would otherwise gain a blank line between every line and have
        # its paragraph structure fall apart.
        path.write_text(response.text, encoding="utf-8", newline="")
        print(f"    downloaded {len(response.text):,} chars -> {path.name}")
    return path.read_text(encoding="utf-8")


async def seed_literature(session, entry: LiteratureEntry) -> tuple[bool, int]:
    try:
        text = literature_text(entry)
    except httpx.HTTPError as exc:
        print(f"    FETCH FAILED: {exc}", file=sys.stderr)
        return False, 0

    adapter = PlainTextLiteratureAdapter(
        text=text,
        title=entry.title,
        source_ref=entry.source_ref,
        source_name="gutenberg",
        author=entry.author,
        source_url=entry.source_url,
        source_file=f"gutenberg-{entry.ebook_id}.txt",
        license_note="Public domain in the USA (Project Gutenberg).",
    )
    try:
        source_work = adapter.load()
    except MalformedSourceError as exc:
        print(f"    COULD NOT PARSE: {exc}", file=sys.stderr)
        return False, 0

    result = await ingest_source_work(session, source_work)
    return result.created, result.content_units


async def seed_anilist(session, entry: AniListEntry, *, slug: str, client: AniListClient):
    fetch = client.fetch_media if slug == "anime" else client.fetch_manga
    adapter_class = AniListAnimeAdapter if slug == "anime" else AniListMangaAdapter

    try:
        media = fetch(entry.anilist_id)
    except AniListNotFoundError as exc:
        print(f"    NOT FOUND: {exc}", file=sys.stderr)
        return None
    except AniListError as exc:
        print(f"    FETCH FAILED: {exc}", file=sys.stderr)
        return None

    source_work = adapter_class(media=media).load()
    return source_work, await ingest_source_work(session, source_work)


async def run(args: argparse.Namespace) -> int:
    client = AniListClient()
    created = 0
    skipped = 0
    failures = 0

    try:
        async with async_session_factory() as session:
            for plan in PLAN:
                if args.domain and plan.slug != args.domain:
                    continue

                print(f"\n=== {plan.slug} ({plan.size} in the manifest) ===")
                fetched = 0

                for entry in plan.literature:
                    print(f"  {entry.title}", flush=True)
                    try:
                        was_created, units = await seed_literature(session, entry)
                    except Exception as exc:  # noqa: BLE001 - one bad work, not a bad run
                        # Isolated deliberately: a manifest of twenty books
                        # should not be stopped by one that will not parse.
                        await session.rollback()
                        session.expunge_all()
                        failures += 1
                        print(f"    INGEST FAILED: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                        continue
                    if was_created:
                        created += 1
                        print(f"    ingested, {units:,} content units", flush=True)
                    else:
                        skipped += 1
                        print("    already present", flush=True)
                    # Durable per work, and the identity map is dropped with
                    # it: a novel is thousands of rows to hold on to.
                    if args.dry_run:
                        await session.rollback()
                    else:
                        await session.commit()
                    session.expunge_all()

                for entry in plan.anilist:
                    if fetched:
                        time.sleep(args.delay)
                    fetched += 1
                    outcome = await seed_anilist(session, entry, slug=plan.slug, client=client)
                    if outcome is None:
                        failures += 1
                        continue
                    source_work, result = outcome
                    if result.created:
                        created += 1
                        print(
                            f"  {source_work.title}: ingested, {result.containers} containers, "
                            f"{result.content_units} content units",
                            flush=True,
                        )
                    else:
                        skipped += 1
                        print(f"  {source_work.title}: already present", flush=True)
                    if args.dry_run:
                        await session.rollback()
                    else:
                        await session.commit()
                    session.expunge_all()

            edges = await resolve_source_relations(session)
            print(f"\nresolved {edges} new source relationship edge(s)")
            print(f"{created} ingested, {skipped} already present, {failures} failed")

            if args.dry_run:
                await session.rollback()
                print("DRY RUN: rolled back")
            else:
                await session.commit()
                print("committed")
    except UnknownDomainError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        await engine.dispose()

    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
