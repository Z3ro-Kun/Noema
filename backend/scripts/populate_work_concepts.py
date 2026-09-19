"""Populate work-level concepts from source-provided labels.

    python -m scripts.populate_work_concepts --dry-run
    python -m scripts.populate_work_concepts --work-id <uuid> --work-id <uuid>
    python -m scripts.populate_work_concepts

Deterministic and reproducible: a work's concepts depend only on that work's
own source metadata, so re-running changes nothing and adding a work to the
corpus never alters another work's concepts.

AniList labels are already in the database from ingestion. Gutenberg subject
headings are fetched per literature work (one small catalogue record each, no
book text) unless --no-fetch is passed.
"""

import argparse
import asyncio
import sys
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.db import async_session_factory, engine
from app.models import Domain, Work
from app.services.concepts.service import (
    anilist_labels,
    apply_source_labels,
    ensure_vocabulary,
    gutenberg_labels,
)
from app.services.ingestion.gutenberg_client import (
    BookNotFoundError,
    GutenbergClient,
    GutenbergError,
)

LITERATURE = "literature"


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
        "--no-fetch",
        action="store_true",
        help="skip Gutenberg lookups and use only labels already stored",
    )
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between fetches")
    parser.add_argument("--dry-run", action="store_true", help="report without committing")
    return parser.parse_args(argv)


def print_report(report) -> None:
    head = f"[{report.domain_slug or '?':11s}] {report.work_title[:44]}"
    if report.no_source_labels:
        print(f"{head}\n    NO SOURCE LABELS -- no concepts (correct for metadata-only works)")
        return
    print(
        f"{head}\n    labels {report.labels_examined:3d} | "
        f"created {report.created} | updated {report.updated} | unchanged {report.unchanged}"
    )
    if report.unmapped:
        shown = ", ".join(report.unmapped[:6])
        more = f" (+{len(report.unmapped) - 6} more)" if len(report.unmapped) > 6 else ""
        print(f"    unmapped {len(report.unmapped):3d}: {shown}{more}")


async def run(args: argparse.Namespace) -> int:
    client = GutenbergClient()
    failures = 0

    try:
        async with async_session_factory() as session:
            created, updated = await ensure_vocabulary(session)
            print(f"vocabulary: {created} concept(s) created, {updated} updated\n")

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
                if domain_slug == LITERATURE:
                    source_ref = (work.external_ids or {}).get("source_ref")
                    if args.no_fetch or not source_ref:
                        labels = []
                    else:
                        if fetched:
                            time.sleep(args.delay)
                        fetched += 1
                        try:
                            metadata = client.fetch_metadata(source_ref)
                            labels = gutenberg_labels(metadata.subjects)
                        except BookNotFoundError as exc:
                            print(f"[literature ] {work.title}\n    NO RECORD: {exc}")
                            failures += 1
                            continue
                        except GutenbergError as exc:
                            print(f"[literature ] {work.title}\n    FETCH FAILED: {exc}",
                                  file=sys.stderr)
                            failures += 1
                            continue
                else:
                    labels = anilist_labels(work)

                report = await apply_source_labels(
                    session, work=work, labels=labels, domain_slug=domain_slug
                )
                print_report(report)

            if args.dry_run:
                await session.rollback()
                print("\ndry run: rolled back")
            else:
                await session.commit()
    finally:
        await engine.dispose()

    return 1 if failures else 0


def main() -> int:
    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
