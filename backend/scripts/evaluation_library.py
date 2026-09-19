"""Materialise or remove the evaluation library.

    python -m scripts.evaluation_library --describe
    python -m scripts.evaluation_library --build
    python -m scripts.evaluation_library --teardown

**This creates synthetic test accounts, not production users.** They exist so
the future preference engine can be inspected by hand against known
behavioural patterns; tests build the same dataset inside a transaction they
roll back, and do not need this command.

Every account uses the reserved `@evaluation.invalid` domain, so `--teardown`
removes exactly what `--build` created and can never match a real user.
Canonical content is only ever referenced, never modified.
"""

import argparse
import asyncio
import sys

from app.core.db import async_session_factory, engine
from app.services import library_service
from tests.evaluation.builder import (
    MissingCorpusWorkError,
    build_evaluation_library,
    teardown_evaluation_library,
)
from tests.evaluation.dataset import EVALUATION_USERS, referenced_works


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--build", action="store_true", help="create the evaluation users")
    action.add_argument("--teardown", action="store_true", help="remove them again")
    action.add_argument(
        "--describe", action="store_true", help="print the documented cases and exit"
    )
    return parser.parse_args(argv)


def describe() -> int:
    print(f"{len(EVALUATION_USERS)} evaluation users, referencing "
          f"{len(referenced_works())} canonical works.\n")
    for spec in EVALUATION_USERS:
        print(f"--- case {spec.case}: {spec.key} ---")
        print(f"  exposure      : {spec.exposure}")
        print(f"  ratings       : {spec.rating_pattern}")
        print(f"  reconsumption : {spec.reconsumption}")
        print(f"  abandonment   : {spec.abandonment}")
        print(f"  expected      : {spec.expectation}")
        print(f"  interactions  : {len(spec.interactions)}\n")
    return 0


async def run(args: argparse.Namespace) -> int:
    try:
        async with async_session_factory() as session:
            if args.teardown:
                removed = await teardown_evaluation_library(session)
                await session.commit()
                print(f"removed {removed} evaluation user(s) and their interactions")
                return 0

            try:
                built = await build_evaluation_library(session)
            except MissingCorpusWorkError as exc:
                print(str(exc), file=sys.stderr)
                print("ingest the corpus first; the dataset is not built partially",
                      file=sys.stderr)
                return 1

            for entry in built:
                rows = await library_service.list_library(
                    session, user_id=entry.user_id, include_removed=True
                )
                ratings = [row.rating for row in rows]
                print(f"[{entry.spec.case}] {entry.spec.email}")
                print(f"     {len(rows)} interaction(s), ratings {ratings}")

            await session.commit()
            print(f"\nbuilt {len(built)} evaluation user(s)")
            print("remove them again with --teardown")
    finally:
        await engine.dispose()
    return 0


def main() -> int:
    args = parse_args()
    if args.describe:
        return describe()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
