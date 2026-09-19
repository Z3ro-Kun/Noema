"""Attach Wikipedia summaries to AniList works already in Noema.

    python -m scripts.ingest_wikipedia_summaries --source-ref 1
    python -m scripts.ingest_wikipedia_summaries --source-ref 1 --page "List of Cowboy Bebop episodes"
    python -m scripts.ingest_wikipedia_summaries --content volumes --source-ref 30642

Works are addressed by their AniList source_ref because AniList owns identity
here; this only adds text to what it already catalogued. Re-running is a no-op
while the Wikipedia page is unchanged.

`--content` selects the structural unit being described: `episodes` reads
{{Episode list}} on an anime's episode-list article, `volumes` reads
{{Graphic novel list}} on a manga's chapter-list article. Everything after
parsing -- rights gate, number-plus-title matching, `summary` tier, TextSource
provenance -- is identical for both.
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.db import async_session_factory, engine
from app.models import Work
from app.services.ingestion.rights import assess_mediawiki_rights
from app.services.ingestion.summary_service import attach_episode_summaries
from app.services.ingestion.wikipedia import (
    episode_list_candidates,
    parse_episode_list,
    parse_volume_list,
    volume_list_candidates,
)
from app.services.ingestion.wikipedia_client import (
    PageNotFoundError,
    WikipediaClient,
    WikipediaError,
)

# content mode -> (container_type, candidate resolver, parser, label)
CONTENT_MODES = {
    "episodes": ("episode", episode_list_candidates, parse_episode_list, "ep"),
    "volumes": ("volume", volume_list_candidates, parse_volume_list, "vol"),
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-ref",
        action="append",
        required=True,
        help="AniList id of an already-ingested work (repeatable)",
    )
    parser.add_argument(
        "--content",
        choices=sorted(CONTENT_MODES),
        default="episodes",
        help="which structural unit the summaries describe (default: episodes)",
    )
    parser.add_argument(
        "--page",
        default=None,
        help="explicit Wikipedia page title, skipping candidate resolution",
    )
    parser.add_argument("--dry-run", action="store_true", help="report without committing")
    return parser.parse_args(argv)


def _english_title(work: Work) -> str | None:
    """The source's English title, where it recorded one."""
    titles = ((work.extra_metadata or {}).get("anilist") or {}).get("titles") or {}
    return titles.get("english")


def print_report(report, label: str = "ep") -> None:
    print(f"\n{report.work_title}")
    if not report.stored:
        print(f"  NOT STORED: {report.not_stored_reason}")
        return

    print(f"  page       : {report.page_title} (rev {report.revision_ref})")
    print(f"  licence    : {report.licence}")
    print(
        f"  containers : {report.containers_examined} examined | "
        f"summaries extracted: {report.summaries_extracted}"
    )
    print(
        f"  attached   : {report.attached} new | {report.unchanged} unchanged | "
        f"{report.superseded} superseded"
    )
    if report.unmatched:
        print(f"  unmatched  : {len(report.unmatched)}")
        for item in report.unmatched[:5]:
            print(f"      {label} {item['episode_number']} ({item['title']}): {item['reason']}")
    if report.parser_skipped:
        print(f"  parser skipped: {len(report.parser_skipped)}")
        for item in report.parser_skipped[:5]:
            print(f"      {item['reason']}: {item.get('title') or item.get('raw_number')!r}")


async def run(args: argparse.Namespace) -> int:
    container_type, resolve_candidates, parse, label = CONTENT_MODES[args.content]
    client = WikipediaClient()
    failures = 0

    try:
        rightsinfo = client.fetch_rightsinfo()
    except WikipediaError as exc:
        print(f"could not read the wiki's licence declaration: {exc}", file=sys.stderr)
        print("refusing to ingest without established rights", file=sys.stderr)
        return 1

    try:
        async with async_session_factory() as session:
            for source_ref in args.source_ref:
                work = (
                    await session.execute(
                        select(Work).where(
                            Work.source == "anilist",
                            Work.external_ids["source_ref"].astext == str(source_ref),
                        )
                    )
                ).scalar_one_or_none()

                if work is None:
                    print(
                        f"no ingested AniList work with id {source_ref}; "
                        "run scripts.ingest_anime or scripts.ingest_manga first",
                        file=sys.stderr,
                    )
                    failures += 1
                    continue

                # AniList's primary title is romaji; English Wikipedia files
                # the same series under its English title. Both are source
                # facts, so both are offered to candidate resolution.
                titles = (work.title, _english_title(work), work.original_title)
                candidates = [args.page] if args.page else resolve_candidates(*titles)
                try:
                    page = client.resolve_page(candidates)
                except PageNotFoundError:
                    # A coverage gap, recorded rather than worked around.
                    print(f"\n{work.title}")
                    print(f"  NO COVERAGE: tried {candidates}")
                    failures += 1
                    continue
                except WikipediaError as exc:
                    print(f"\n{work.title}\n  FETCH FAILED: {exc}", file=sys.stderr)
                    failures += 1
                    continue

                rights = assess_mediawiki_rights(
                    rightsinfo,
                    page_title=page.title,
                    page_url=page.url,
                    revision_ref=page.revid,
                )
                parsed = parse(page.wikitext)

                report = await attach_episode_summaries(
                    session,
                    work_id=work.id,
                    work_title=work.title,
                    parsed=parsed,
                    rights=rights,
                    page_title=page.title,
                    page_url=page.url,
                    revision_ref=page.revid,
                    retrieved_text=page.wikitext,
                    container_type=container_type,
                )
                print_report(report, label)

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
