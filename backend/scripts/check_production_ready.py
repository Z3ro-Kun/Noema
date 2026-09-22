"""Is the database this is pointed at ready to serve as production?

    python -m scripts.check_production_ready

Read-only. It deletes nothing, writes nothing, and is safe to run against a
development database -- where it is *expected* to report user rows, because a
development database has a developer's own library in it. That is the point:
the same command answers "is this clean?" wherever it is aimed, and the answer
for a laptop is no.

Two questions, and they are separate:

    the catalogue is there     schema at the release revision, the corpus
                               ingested, embeddings present. `validate_corpus`
                               checks this in far more detail; what is
                               repeated here is only enough to catch an
                               empty database.

    and nothing else is        no accounts, no libraries, no ratings, no
                               feedback. A production database that starts
                               with somebody's test history is a privacy
                               problem before it is a data problem, and the
                               evaluation fixtures create exactly that shape
                               of row.

Exit code 0 only when both hold.
"""

import argparse
import asyncio

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import async_session_factory, engine
from app.models import (
    Concept,
    Embedding,
    User,
    UserContentEvent,
    UserContentInteraction,
    UserPreferenceFeedback,
    UserRecommendationFeedback,
    UserSession,
    Work,
    WorkConcept,
)

RELEASE_REVISION = "0013"

# Every table that holds something a person did. All of them must be empty in
# a database that has never served anyone.
USER_TABLES = (
    ("users", User),
    ("user_sessions", UserSession),
    ("user_content_interactions", UserContentInteraction),
    ("user_content_events", UserContentEvent),
    ("user_preference_feedback", UserPreferenceFeedback),
    ("user_recommendation_feedback", UserRecommendationFeedback),
)

# What the catalogue must contain before the product is worth serving.
CATALOGUE_TABLES = (
    ("works", Work),
    ("concepts", Concept),
    ("work_concepts", WorkConcept),
    ("embeddings", Embedding),
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-user-data",
        action="store_true",
        help="report user rows without failing (for checking a development database)",
    )
    return parser.parse_args(argv)


async def count(session: AsyncSession, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def run(args: argparse.Namespace) -> int:
    failures: list[str] = []
    settings = get_settings()

    async with async_session_factory() as session:
        print(f"\nenvironment : {settings.environment}")
        for problem in settings.production_problems():
            print(f"  [warn] {problem}")

        revision = (
            await session.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one_or_none()
        ok = revision == RELEASE_REVISION
        print(f"\n  [{'ok' if ok else 'FAIL'}] schema at {RELEASE_REVISION} -- found {revision}")
        if not ok:
            failures.append("schema revision")

        print("\n=== catalogue ===")
        for label, model in CATALOGUE_TABLES:
            rows = await count(session, model)
            ok = rows > 0
            print(f"  [{'ok' if ok else 'FAIL'}] {label:20} {rows}")
            if not ok:
                failures.append(f"{label} is empty")

        print("\n=== user data (expected: none) ===")
        for label, model in USER_TABLES:
            rows = await count(session, model)
            ok = rows == 0 or args.allow_user_data
            marker = "ok" if rows == 0 else ("info" if args.allow_user_data else "FAIL")
            print(f"  [{marker}] {label:32} {rows}")
            if not ok:
                failures.append(f"{label} holds {rows} rows")

    await engine.dispose()

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
        print("This database is not a clean production database.")
        return 1
    print("ready: catalogue present, no user data")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
