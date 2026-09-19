"""Phase 1Q: compare baseline and frequency-weighted preference evidence.

    python -m scripts.run_idf_experiment
    python -m scripts.run_idf_experiment --smoothing 1 --smoothing 2 --smoothing 5
    python -m scripts.run_idf_experiment --json data/evaluation/idf_experiment.json

An **evaluation artifact, not a product surface.** It builds the Phase 1N
evaluation library inside a transaction, computes both formulations for every
case, writes a machine-readable comparison, and rolls the whole thing back.
Nothing is committed and no user data survives the run.

The comparison deliberately reports the naive `evidence x idf` variant as
well as the salience formulation, so the argument for rejecting the former
rests on measured numbers.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.core.db import async_session_factory, engine
from app.services.preference.experiment import build_weighted_profile
from app.services.preference.frequency import (
    FrequencyParameters,
    load_corpus_frequencies,
)
from tests.evaluation.builder import MissingCorpusWorkError, build_evaluation_library


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoothing",
        action="append",
        type=float,
        default=None,
        help="idf smoothing constant k (repeatable, for a sensitivity sweep)",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="write the full comparison here as JSON",
    )
    parser.add_argument(
        "--min-rated",
        type=int,
        default=1,
        help="only report concepts supported by at least this many rated works",
    )
    return parser.parse_args(argv)


def rank_changes(profile, min_rated: int) -> dict:
    """How far the ordering moves between the two formulations."""
    baseline = [
        item.concept_slug
        for item in profile.ranked_by_baseline()
        if item.works_rated >= min_rated
    ]
    weighted = [
        item.concept_slug
        for item in profile.ranked_by_salience()
        if item.works_rated >= min_rated
    ]
    positions = {slug: index for index, slug in enumerate(baseline)}
    moves = {slug: positions[slug] - index for index, slug in enumerate(weighted)}

    return {
        "baseline_top_5": baseline[:5],
        "weighted_top_5": weighted[:5],
        "largest_rises": sorted(moves.items(), key=lambda item: -item[1])[:3],
        "largest_falls": sorted(moves.items(), key=lambda item: item[1])[:3],
    }


async def run(args: argparse.Namespace) -> int:
    smoothings = args.smoothing or [2.0]
    report: dict = {"smoothings": {}}

    try:
        async with async_session_factory() as session:
            try:
                built = await build_evaluation_library(session)
            except MissingCorpusWorkError as exc:
                print(str(exc), file=sys.stderr)
                return 1

            frequencies = await load_corpus_frequencies(session)
            report["corpus"] = {
                "total_works": frequencies.total_works,
                "concepts_with_works": len(frequencies.by_slug),
                "document_frequency": {
                    slug: entry.document_frequency
                    for slug, entry in sorted(
                        frequencies.by_slug.items(),
                        key=lambda item: -item[1].document_frequency,
                    )
                },
            }

            for smoothing in smoothings:
                parameters = FrequencyParameters(smoothing=smoothing)
                cases: dict = {}

                for entry in built:
                    profile = await build_weighted_profile(
                        session, entry.user_id, frequency_parameters=parameters
                    )
                    rated = [
                        item
                        for item in profile.concepts
                        if item.works_rated >= args.min_rated
                    ]
                    cases[entry.spec.case] = {
                        "expectation": entry.spec.expectation,
                        "concepts": [
                            {
                                "concept": item.concept_slug,
                                "document_frequency": item.document_frequency,
                                "idf": item.idf,
                                "specificity": item.specificity,
                                "baseline_evidence": item.preference_evidence,
                                "baseline_direction": item.direction,
                                "baseline_confidence": item.confidence,
                                "works_rated": item.works_rated,
                                "weighted_salience": item.salience,
                                "weighted_direction": item.direction,
                                "weighted_confidence": item.confidence,
                                "naive_weighted_evidence": item.naive_weighted_evidence,
                                "naive_out_of_range": item.naive_out_of_range,
                            }
                            for item in profile.ranked_by_salience()
                            if item.works_rated >= args.min_rated
                        ],
                        "ranking": rank_changes(profile, args.min_rated),
                        "naive_out_of_range_count": sum(
                            1 for item in rated if item.naive_out_of_range
                        ),
                        "rated_concept_count": len(rated),
                    }

                report["smoothings"][str(smoothing)] = cases
                print(f"\n=== smoothing k = {smoothing} ===")
                for case, data in cases.items():
                    ranking = data["ranking"]
                    print(f"  case {case}: {data['rated_concept_count']} rated concepts, "
                          f"{data['naive_out_of_range_count']} naive values outside [-1,1]")
                    print(f"      baseline top: {ranking['baseline_top_5'][:3]}")
                    print(f"      weighted top: {ranking['weighted_top_5'][:3]}")

            # Never commit: the evaluation users exist only for this run.
            await session.rollback()
    finally:
        await engine.dispose()

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")

    return 0


def main() -> int:
    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
