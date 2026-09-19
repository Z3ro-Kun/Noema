"""Populate `cover_image_url` on works already in the corpus.

    python -m scripts.enrich_covers --dry-run
    python -m scripts.enrich_covers
    python -m scripts.enrich_covers --work-id <uuid> --work-id <uuid>

Phase 1AA. The ingestion adapters now read cover art from their own sources
(`Media.coverImage` for AniList, the `pgterms:file` image entry for
Gutenberg), so anything ingested from here on carries a cover already. This
script is for the corpus that was ingested before they did: it re-asks each
work's *own* source for that one field and writes it onto the existing row.

What it touches, and nothing else:

    extra_metadata["cover_image_url"]                 the URL
    extra_metadata["provenance"]["cover_image"]       where it came from

Titles, identifiers, descriptions, containers, content units, concepts and
embeddings are never read for writing and never modified. A work keeps its
id, so nothing downstream of it moves.

Deterministic and repeatable: the URL depends only on what the source
returns, so a second run over an unchanged source reports every work as
unchanged and commits nothing new. Neither source is asked for anything but
the record it already publishes for that work, and no image file is ever
downloaded -- the URL is stored and the browser fetches it.
"""

import argparse
import asyncio
import sys
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.core.db import async_session_factory, engine
from app.models import Domain, Work
from app.services.ingestion.anilist_client import (
    AniListClient,
    AniListError,
    AniListNotFoundError,
)
from app.services.ingestion.anime import cover_image
from app.services.ingestion.gutenberg_client import (
    BookNotFoundError,
    GutenbergClient,
    GutenbergError,
)

LITERATURE = "literature"
ANIME = "anime"
MANHWA = "manhwa"

GUTENBERG_LICENSE_NOTE = (
    "Cover image published by Project Gutenberg with the public-domain "
    "ebook record. Referenced by URL only, never copied."
)


@dataclass
class CoverReport:
    work_title: str
    domain_slug: str
    outcome: str  # "set" | "unchanged" | "changed" | "none" | "failed"
    url: str | None = None
    note: str | None = None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-id",
        action="append",
        type=uuid.UUID,
        default=None,
        help="limit to specific works (repeatable); default is the whole corpus",
    )
    parser.add_argument(
        "--delay", type=float, default=1.0, help="seconds between source fetches"
    )
    parser.add_argument("--dry-run", action="store_true", help="report without committing")
    return parser.parse_args(argv)


def _fetch_cover(
    work: Work,
    domain_slug: str,
    *,
    anilist: AniListClient,
    gutenberg: GutenbergClient,
) -> tuple[str | None, dict | None]:
    """Ask this work's own source for its cover. Raises the source's error."""
    source_ref = (work.external_ids or {}).get("source_ref")
    if not source_ref:
        return None, None

    if domain_slug == LITERATURE:
        metadata = gutenberg.fetch_metadata(source_ref)
        if not metadata.cover_image_url:
            return None, None
        return metadata.cover_image_url, {
            "source_field": metadata.cover_image_source_field,
            "source_url": metadata.source_url,
            "license_note": GUTENBERG_LICENSE_NOTE,
        }

    media = (
        anilist.fetch_media(int(source_ref))
        if domain_slug == ANIME
        else anilist.fetch_manga(int(source_ref))
    )
    return cover_image(media)


def _apply(work: Work, url: str | None, provenance: dict | None) -> str:
    """Write the cover onto the work. Returns what happened."""
    if url is None:
        return "none"

    metadata = dict(work.extra_metadata or {})
    existing = metadata.get("cover_image_url")

    metadata["cover_image_url"] = url
    # Inside the record's existing provenance block rather than beside it:
    # this is one more fact about where this work's metadata came from, and
    # a separate media-provenance system would be a second answer to the
    # same question.
    existing_provenance = dict(metadata.get("provenance") or {})
    if provenance:
        existing_provenance["cover_image"] = provenance
    metadata["provenance"] = existing_provenance

    work.extra_metadata = metadata
    # JSONB reassignment is a new dict, but be explicit rather than relying
    # on SQLAlchemy noticing the identity change.
    flag_modified(work, "extra_metadata")

    if existing == url:
        return "unchanged"
    return "changed" if existing else "set"


def print_report(report: CoverReport) -> None:
    head = f"[{report.domain_slug:11s}] {report.work_title[:44]}"
    if report.outcome == "none":
        print(f"{head}\n    NO COVER ON RECORD -- left without one")
        return
    if report.outcome == "failed":
        print(f"{head}\n    FETCH FAILED: {report.note}", file=sys.stderr)
        return
    print(f"{head}\n    {report.outcome:9s} {report.url}")


async def run(args: argparse.Namespace) -> int:
    anilist = AniListClient()
    gutenberg = GutenbergClient()
    counts: dict[str, int] = {}
    failures = 0

    try:
        async with async_session_factory() as session:
            query = (
                select(Work, Domain.slug)
                .join(Domain, Work.domain_id == Domain.id)
                .options(selectinload(Work.domain))
                .order_by(Domain.slug, Work.title)
            )
            if args.work_id:
                query = query.where(Work.id.in_(args.work_id))
            rows = (await session.execute(query)).all()

            fetched = 0
            for work, domain_slug in rows:
                if fetched:
                    time.sleep(args.delay)
                fetched += 1
                try:
                    url, provenance = _fetch_cover(
                        work, domain_slug, anilist=anilist, gutenberg=gutenberg
                    )
                except (BookNotFoundError, AniListNotFoundError, GutenbergError, AniListError) as exc:
                    failures += 1
                    print_report(
                        CoverReport(work.title, domain_slug, "failed", note=str(exc))
                    )
                    continue

                outcome = _apply(work, url, provenance)
                counts[outcome] = counts.get(outcome, 0) + 1
                print_report(CoverReport(work.title, domain_slug, outcome, url=url))

            summary = " | ".join(f"{name} {n}" for name, n in sorted(counts.items()))
            print(f"\n{len(rows)} work(s): {summary or 'nothing to do'}")
            if failures:
                print(f"{failures} source fetch(es) failed", file=sys.stderr)

            if args.dry_run:
                await session.rollback()
                print("dry run: rolled back")
            else:
                await session.commit()
    finally:
        await engine.dispose()

    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
