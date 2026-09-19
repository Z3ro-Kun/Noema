"""Phase 1R: compare the two preference-page orderings on identical evidence.

    python -m scripts.run_ordering_experiment
    python -m scripts.run_ordering_experiment --json data/evaluation/ordering_experiment.json

An **evaluation artifact, not a product surface.** It builds the Phase 1N
evaluation library inside a transaction, builds each case's overview twice --
once under Phase 1P's ordering, once under Phase 1R's -- and rolls the whole
thing back. Nothing is committed and no user data survives the run.

Both overviews come from one `build_preference_profile` call per ordering
against the same fixtures, so every value except the sequence is identical by
construction. The script verifies that rather than assuming it: if the two
runs ever disagree on a concept's direction, band or counts, it says so and
exits non-zero.

Two things it deliberately reports and does not score:

  The qualitative top five, with each signal's supporting work titles and
  domains, so an ordering can be *read* instead of judged by rank number.

  Each top-five concept's document frequency, purely as a label for the
  broad-concept question Phase 1Q left open. Frequency plays no part in
  either ordering; it is here so the trade-off can be seen.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.core.db import async_session_factory, engine
from app.models import Concept, WorkConcept
from app.services.preference.evidence import DIRECTION_UNKNOWN, build_preference_profile
from app.services.preference.ordering import (
    ORDERING_CONFIDENCE_FIRST,
    ORDERING_EVIDENCE_FIRST,
    engine_key,
)
from app.services.preference.product import build_preference_overview
from tests.evaluation.builder import MissingCorpusWorkError, build_evaluation_library

# The concept each case is documented to be about, from `dataset.py`. Cases
# C, D and F have no target by design: C and D have no rating direction at
# all, and F exists to check that nothing generalises from a mixed history.
TARGET_CONCEPTS = {
    "A": "psychological-depth",
    "B": "psychological-depth",
    "C": None,
    "D": None,
    "E": "crime-and-investigation",
    "F": None,
    "G": "science-fiction",
    "H": "science-fiction",
    "I": "science-fiction",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="write the full comparison here as JSON",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help="how many signals the qualitative inspection shows per case",
    )
    return parser.parse_args(argv)


async def document_frequencies(session) -> dict[str, int]:
    """How many works carry each concept. **Reporting only.**

    Read straight from the tables rather than through Phase 1Q's frequency
    module, so nothing here can be mistaken for promoting that machinery.
    """
    rows = (
        await session.execute(
            select(Concept.slug, func.count(func.distinct(WorkConcept.work_id)))
            .join(WorkConcept, WorkConcept.concept_id == Concept.id)
            .group_by(Concept.slug)
        )
    ).all()
    return {slug: count for slug, count in rows}


def signal_row(signal, rank: int, frequencies: dict[str, int]) -> dict:
    """One signal, as both orderings describe it. Only `rank` can differ."""
    return {
        "rank": rank,
        "concept": signal.concept_slug,
        "concept_name": signal.concept_name,
        "direction": signal.direction,
        "confidence_band": signal.confidence_band,
        "works_rated": signal.evidence.works_rated,
        "works_completed": signal.evidence.works_completed,
        "rating_mean": signal.evidence.rating_mean,
        "document_frequency": frequencies.get(signal.concept_slug, 0),
        "supporting_works": [
            {
                "title": work.title,
                "domain": work.domain_name,
                "rating": work.rating,
            }
            for work in signal.contributions
        ],
    }


def comparable(signal) -> dict:
    """Everything about a signal except where it sits in the list.

    Two orderings must produce identical values for this; the whole claim of
    the phase is that only the sequence moved.
    """
    return {
        "concept": signal.concept_slug,
        "concept_name": signal.concept_name,
        "concept_type": signal.concept_type,
        "direction": signal.direction,
        "confidence_band": signal.confidence_band,
        "evidence": signal.evidence.model_dump(),
        "contributions": [work.model_dump(mode="json") for work in signal.contributions],
    }


def rank_of(signals, slug: str | None) -> int | None:
    if slug is None:
        return None
    for index, signal in enumerate(signals, start=1):
        if signal.concept_slug == slug:
            return index
    return None


async def engine_only_rank(session, user_id, slug: str | None) -> int | None:
    """Where the target sits in the *engine's* order, before the product layer.

    Phase 1Q measured its baseline here, on `PreferenceProfile.concepts`, not
    on the overview the reader actually receives -- so its baseline numbers
    exclude the band grouping `product.py` has applied since Phase 1P.
    Reported alongside the real baseline so the two sets of numbers can be
    reconciled from a committed script instead of from memory.
    """
    if slug is None:
        return None
    profile = await build_preference_profile(session, user_id)
    rated = [
        item for item in profile.concepts if item.direction != DIRECTION_UNKNOWN
    ]
    for index, item in enumerate(sorted(rated, key=engine_key), start=1):
        if item.concept_slug == slug:
            return index
    return None


async def run(args: argparse.Namespace) -> int:
    report: dict = {"cases": {}}
    divergences: list[str] = []

    try:
        async with async_session_factory() as session:
            try:
                built = await build_evaluation_library(session)
            except MissingCorpusWorkError as exc:
                print(str(exc), file=sys.stderr)
                return 1

            frequencies = await document_frequencies(session)

            for entry in built:
                case = entry.spec.case
                baseline = await build_preference_overview(
                    session, entry.user_id, ordering=ORDERING_EVIDENCE_FIRST
                )
                candidate = await build_preference_overview(
                    session, entry.user_id, ordering=ORDERING_CONFIDENCE_FIRST
                )

                # Semantic invariance, checked rather than asserted in prose.
                left = {item.concept_slug: comparable(item) for item in baseline.signals}
                right = {item.concept_slug: comparable(item) for item in candidate.signals}
                if left != right:
                    differing = sorted(
                        slug
                        for slug in set(left) | set(right)
                        if left.get(slug) != right.get(slug)
                    )
                    divergences.append(f"case {case}: {differing}")

                awaiting_same = [item.concept_slug for item in baseline.awaiting_ratings] == [
                    item.concept_slug for item in candidate.awaiting_ratings
                ]
                if not awaiting_same:
                    divergences.append(f"case {case}: unrated exposure list reordered")

                target = TARGET_CONCEPTS[case]
                report["cases"][case] = {
                    "expectation": entry.spec.expectation,
                    "target_concept": target,
                    "signal_count": len(baseline.signals),
                    "awaiting_count": len(baseline.awaiting_ratings),
                    "target_rank": {
                        "engine_only": await engine_only_rank(
                            session, entry.user_id, target
                        ),
                        "evidence_first": rank_of(baseline.signals, target),
                        "confidence_first": rank_of(candidate.signals, target),
                    },
                    "evidence_first": [
                        signal_row(item, index, frequencies)
                        for index, item in enumerate(baseline.signals, start=1)
                    ],
                    "confidence_first": [
                        signal_row(item, index, frequencies)
                        for index, item in enumerate(candidate.signals, start=1)
                    ],
                    "semantically_identical": left == right and awaiting_same,
                }

            # Never commit: the evaluation users exist only for this run.
            await session.rollback()
    finally:
        await engine.dispose()

    report["semantically_identical"] = not divergences
    report["divergences"] = divergences

    for case, data in report["cases"].items():
        target = data["target_concept"]
        ranks = data["target_rank"]
        print(f"\n=== case {case} -- {data['signal_count']} signals, "
              f"{data['awaiting_count']} awaiting ratings ===")
        if target:
            print(f"  target {target}: "
                  f"{ranks['evidence_first']} -> {ranks['confidence_first']} "
                  f"(engine order alone: {ranks['engine_only']})")
        else:
            print("  no documented target concept")

        for label in ("evidence_first", "confidence_first"):
            print(f"  {label}:")
            for row in data[label][: args.top]:
                works = ", ".join(
                    f"{work['title']} ({work['domain']}"
                    + (f" {work['rating']}/10)" if work["rating"] is not None else ", unrated)")
                    for work in row["supporting_works"]
                )
                print(f"    {row['rank']}. {row['concept']} [{row['direction']}, "
                      f"{row['confidence_band']}, {row['works_rated']} rated, "
                      f"df={row['document_frequency']}]")
                print(f"       {works}")

    if divergences:
        print("\nSEMANTIC DIVERGENCE -- the orderings changed more than order:",
              file=sys.stderr)
        for line in divergences:
            print(f"  {line}", file=sys.stderr)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")

    return 1 if divergences else 0


def main() -> int:
    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
