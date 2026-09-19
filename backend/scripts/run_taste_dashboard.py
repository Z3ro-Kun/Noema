"""Phase 1V: the taste dashboard each evaluation case produces.

    python -m scripts.run_taste_dashboard
    python -m scripts.run_taste_dashboard --json data/evaluation/taste_dashboard.json

An **evaluation artifact, not a product surface.** It builds the evaluation
library inside a transaction, composes each case's dashboard, prints it and
rolls the whole thing back. Nothing is committed and no user data survives.

The JSON it writes is the shape a user-facing serializer would emit: groups,
display names, confidence bands, counts, controlled keys and supporting work
ids. The internal `preference_evidence` and `confidence` floats are printed in
the terminal view, because this file exists to let a developer check the
grouping, and omitted from the JSON, because that is the boundary a product
DTO has to hold.

No sentence is generated anywhere. The keys are printed as keys.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.core.db import async_session_factory, engine
from app.services.preference.dashboard import build_taste_dashboard
from tests.evaluation.builder import MissingCorpusWorkError, build_evaluation_library


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=None, help="write the result here")
    return parser.parse_args(argv)


def item_row(item) -> dict:
    """What a product serializer would send. No raw evidence floats."""
    return {
        "key": item.key,
        "display_name": item.display_name,
        "features": [
            {"key": f.key, "name": f.name, "family": f.family} for f in item.features
        ],
        "kind": item.kind,
        "direction": item.direction,
        "bucket": item.bucket,
        "confidence_band": item.confidence_band,
        "presentation_key": item.presentation_key,
        "domains": list(item.domains),
        "is_cross_domain": item.is_cross_domain,
        "also_supported_by": list(item.also_supported_by),
        "evidence": {
            "works_rated": item.evidence.works_rated,
            "works_completed": item.evidence.works_completed,
            "works_exposed": item.evidence.works_exposed,
            "rating_mean": item.evidence.rating_mean,
            "works_reconsumed": item.evidence.works_reconsumed,
            "total_completions": item.evidence.total_completions,
            "works_abandoned": item.evidence.works_abandoned,
            "works_on_hold": item.evidence.works_on_hold,
        },
        "supporting_work_ids": [str(value) for value in item.supporting_work_ids],
    }


def standout_row(observation) -> dict:
    return {
        "observation": observation.observation,
        "presentation_key": observation.presentation_key,
        "features": [
            {"key": f.key, "name": f.name} for f in observation.features
        ],
        "pattern_keys": list(observation.pattern_keys),
        "domains": list(observation.domains),
        "confidence_band": observation.confidence_band,
        "works_rated": observation.works_rated,
    }


async def run(args: argparse.Namespace) -> int:
    report: dict = {"cases": {}}

    try:
        async with async_session_factory() as session:
            try:
                built = await build_evaluation_library(session)
            except MissingCorpusWorkError as exc:
                print(str(exc), file=sys.stderr)
                return 1

            for entry in built:
                result = await build_taste_dashboard(session, entry.user_id)
                summary = result.summary
                report["cases"][entry.spec.case] = {
                    "expectation": entry.spec.expectation,
                    "strongly_likes": [item_row(i) for i in result.strongly_likes],
                    "mildly_likes": [item_row(i) for i in result.mildly_likes],
                    "dislikes": [item_row(i) for i in result.dislikes],
                    "emerging": [item_row(i) for i in result.emerging],
                    "what_stands_out": [
                        standout_row(o) for o in result.what_stands_out
                    ],
                    "summary": {
                        "works_rated": summary.works_rated,
                        "concepts_with_established_evidence": (
                            summary.concepts_with_established_evidence
                        ),
                        "strongly_likes": summary.strongly_likes,
                        "mildly_likes": summary.mildly_likes,
                        "dislikes": summary.dislikes,
                        "emerging_signals": summary.emerging_signals,
                        "standouts": summary.standouts,
                    },
                }

                print(
                    f"\n=== case {entry.spec.case}: {summary.works_rated} works rated, "
                    f"{summary.concepts_with_established_evidence} established "
                    f"({summary.strongly_likes} strong, {summary.mildly_likes} mild, "
                    f"{summary.dislikes} disliked), "
                    f"{summary.emerging_signals} emerging ==="
                )
                for label, group in (
                    ("Strongly likes", result.strongly_likes),
                    ("Mildly likes", result.mildly_likes),
                    ("Dislikes", result.dislikes),
                ):
                    if not group:
                        continue
                    print(f"  {label}")
                    for item in group:
                        alternatives = (
                            f"  [also: {', '.join(item.also_supported_by)}]"
                            if item.also_supported_by
                            else ""
                        )
                        print(
                            f"    {item.display_name:44} {item.confidence_band:9} "
                            f"n={item.evidence.works_rated} "
                            f"ev={item.evidence.preference_evidence:+.3f} "
                            f"conf={item.evidence.confidence:.3f} "
                            f"{'/'.join(item.domains)}{alternatives}"
                        )
                if result.emerging:
                    print(f"  Early signals ({len(result.emerging)})")
                    for item in result.emerging[:3]:
                        print(
                            f"    {item.display_name:44} {item.direction:8} "
                            f"n={item.evidence.works_rated}"
                        )
                for observation in result.what_stands_out:
                    features = " + ".join(f.name for f in observation.features) or "--"
                    print(
                        f"  stands out: {observation.observation:22} "
                        f"{observation.presentation_key:26} {features}"
                    )

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
