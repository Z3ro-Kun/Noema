"""Re-apply the display-title rule to works already in the corpus.

    python -m scripts.reselect_titles --dry-run
    python -m scripts.reselect_titles

Phase 1AB. The AniList adapters used to take the romaji title as the work's
name, so a Korean webtoon arrived as "Na Honjaman Level Up" while AniList's
own English title -- "Solo Leveling" -- sat unused in the record. The rule is
now english -> romaji -> native, and this brings the existing rows into line
with it.

Nothing is fetched. Every title this writes is already in the work's own
`extra_metadata.anilist.titles`, captured at ingestion, so this is a
re-selection rather than an enrichment.

What it touches, and nothing else:

    works.title      the display title, re-chosen by the new rule

`original_title` is left exactly as it is: the adapters have always stored
the native title there, and it is the thing a reader loses if this is done
carelessly. Work ids, external ids, descriptions, containers, content units,
concepts and embeddings are never read for writing. Titles are not embedded
-- the vectors are built from content unit text -- so nothing about
retrieval can move as a result of running this.

Literature is untouched: Gutenberg supplies one title and there is nothing
to choose between.
"""

import argparse
import asyncio

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.db import async_session_factory, engine
from app.models import Domain, Work

# The order the adapters now use. English is what a reader recognises;
# romaji is always present in practice; native is the last resort.
PREFERENCE = ("english", "romaji", "native")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report without committing")
    return parser.parse_args(argv)


def chosen_title(work: Work) -> tuple[str | None, str | None]:
    """The title the current rule would pick, and which variant it is."""
    titles = ((work.extra_metadata or {}).get("anilist") or {}).get("titles") or {}
    for variant in PREFERENCE:
        value = titles.get(variant)
        if isinstance(value, str) and value.strip():
            return value.strip(), variant
    return None, None


async def run(args: argparse.Namespace) -> int:
    changed = 0
    try:
        async with async_session_factory() as session:
            works_before = (
                await session.execute(select(func.count()).select_from(Work))
            ).scalar_one()

            rows = (
                (
                    await session.execute(
                        select(Work)
                        .options(selectinload(Work.domain))
                        .join(Domain, Work.domain_id == Domain.id)
                        .order_by(Domain.slug, Work.title)
                    )
                )
                .scalars()
                .all()
            )

            for work in rows:
                title, variant = chosen_title(work)
                if title is None:
                    print(f"[{work.domain.slug:11s}] {work.title[:40]:40} no AniList titles — left alone")
                    continue
                if title == work.title:
                    print(f"[{work.domain.slug:11s}] {work.title[:40]:40} unchanged ({variant})")
                    continue

                print(
                    f"[{work.domain.slug:11s}] {work.title[:40]:40} -> {title} ({variant})"
                )
                work.title = title
                changed += 1

            await session.flush()

            works_after = (
                await session.execute(select(func.count()).select_from(Work))
            ).scalar_one()
            assert works_after == works_before, "no work may be created or removed"

            print(f"\n{len(rows)} work(s) examined, {changed} retitled")
            if args.dry_run:
                await session.rollback()
                print("DRY RUN: rolled back (omit --dry-run to commit)")
            else:
                await session.commit()
                print("committed")
    finally:
        await engine.dispose()

    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
