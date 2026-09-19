"""Ingest manga/manhwa works from AniList by id.

    python -m scripts.ingest_manga 30642 30025 98416 105398

Fetches each work's metadata, normalizes it, stores it, then resolves the
source-provided relations between everything ingested so far. Re-running is a
no-op for works already present.

Only metadata is retrieved. AniList has no chapter text; volume summaries,
where Wikipedia has them, are attached separately by
`scripts.ingest_wikipedia_summaries --content volumes`.
"""

import argparse
import asyncio
import sys
import time

from app.core.db import async_session_factory, engine
from app.services.ingestion.anilist_client import (
    AniListClient,
    AniListError,
    AniListNotFoundError,
)
from app.services.ingestion.manga import AniListMangaAdapter
from app.services.ingestion.service import (
    UnknownDomainError,
    ingest_source_work,
    resolve_source_relations,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("anilist_ids", nargs="+", type=int, help="AniList manga ids")
    parser.add_argument(
        "--delay", type=float, default=1.0, help="seconds between API calls"
    )
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    client = AniListClient()
    failures = 0

    try:
        async with async_session_factory() as session:
            for index, anilist_id in enumerate(args.anilist_ids):
                if index:
                    time.sleep(args.delay)

                try:
                    media = client.fetch_manga(anilist_id)
                except AniListNotFoundError as exc:
                    print(f"skipping {anilist_id}: {exc}", file=sys.stderr)
                    failures += 1
                    continue
                except AniListError as exc:
                    print(f"failed {anilist_id}: {exc}", file=sys.stderr)
                    failures += 1
                    continue

                source_work = AniListMangaAdapter(media=media).load()
                try:
                    result = await ingest_source_work(session, source_work)
                except UnknownDomainError as exc:
                    print(str(exc), file=sys.stderr)
                    return 1

                tradition = source_work.extra_metadata["anilist"]["comic_tradition"]
                if result.created:
                    print(
                        f"ingested '{source_work.title}' ({anilist_id}, {tradition}): "
                        f"{result.containers} volumes, {result.content_units} content units, "
                        f"{result.entities} characters, "
                        f"{result.relations_recorded} source relations recorded"
                    )
                else:
                    print(f"'{source_work.title}' ({anilist_id}) already ingested; skipping")

            edges = await resolve_source_relations(session)
            print(f"resolved {edges} new source relationship edge(s)")
            await session.commit()
    finally:
        await engine.dispose()

    return 1 if failures else 0


def main() -> int:
    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
