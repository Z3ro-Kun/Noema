"""Phase 1T: what a composed taste profile looks like for each evaluation case.

    python -m scripts.run_profile_composition
    python -m scripts.run_profile_composition --json data/evaluation/profile_composition.json

An **evaluation artifact, not a product surface.** It builds the evaluation
library inside a transaction, composes every case's profile, prints it, and
rolls the whole thing back. Nothing is committed and no user data survives.

What it emits is deliberately narrower than what the layer holds: supporting
works appear with the rating the reader gave, and the normalized value the
engine derived from it does not. That is the boundary a later product DTO
will have to hold anyway, and modelling it here keeps the artifact honest
about what belongs in front of a person.

Selection diagnostics are included because this file is for debugging. They
are not a user-facing surface and nothing renders them.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.core.db import async_session_factory, engine
from app.services.preference.profile import compose_profile
from app.services.preference.taste import KIND_COMBINATION
from tests.evaluation.builder import MissingCorpusWorkError, build_evaluation_library


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=None, help="write the result here")
    parser.add_argument(
        "--works",
        type=int,
        default=4,
        help="supporting works printed per pattern",
    )
    return parser.parse_args(argv)


def selected_row(selected) -> dict:
    pattern = selected.pattern
    return {
        "pattern": pattern.key,
        "features": [
            {"key": f.key, "name": f.name, "family": f.family} for f in pattern.features
        ],
        "kind": pattern.kind,
        "status": pattern.status,
        "direction": pattern.direction,
        "confidence": pattern.confidence,
        "preference_evidence": pattern.preference_evidence,
        "works_rated": pattern.works_rated,
        "works_reconsumed": pattern.works_reconsumed,
        "total_completions": pattern.total_completions,
        "works_abandoned": pattern.works_abandoned,
        "works_on_hold": pattern.works_on_hold,
        "domains": list(pattern.domains),
        "indistinguishable_from": list(selected.indistinguishable_from),
        "supporting_works": [
            {
                "title": w.title,
                "domain": w.domain_slug,
                "rating": w.rating,
                "times_completed": w.times_completed,
                "in_library": w.in_library,
            }
            for w in pattern.supporting_works
        ],
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
                composed = await compose_profile(session, entry.user_id)
                diagnostics = composed.diagnostics
                report["cases"][entry.spec.case] = {
                    "expectation": entry.spec.expectation,
                    "key_patterns": [selected_row(s) for s in composed.key_patterns],
                    "early_signals": [selected_row(s) for s in composed.early_signals],
                    "insights": [
                        {
                            "type": i.type,
                            "pattern_keys": list(i.pattern_keys),
                            "domains": list(i.domains),
                            "detail": i.detail,
                        }
                        for i in composed.insights
                    ],
                    "diagnostics": {
                        "candidates": diagnostics.candidates,
                        "eligible": diagnostics.eligible,
                        "distinct_support_sets": diagnostics.distinct_support_sets,
                        "selected": diagnostics.selected,
                        "rejected": diagnostics.rejected,
                        "indistinguishable_groups": [
                            list(group)
                            for group in diagnostics.indistinguishable_groups
                        ],
                        "individual_guarantee_applied": (
                            diagnostics.individual_guarantee_applied
                        ),
                    },
                }

                print(
                    f"\n=== case {entry.spec.case}: "
                    f"{len(composed.key_patterns)} key, "
                    f"{len(composed.early_signals)} early, "
                    f"{len(composed.insights)} insights "
                    f"(from {diagnostics.candidates} candidates, "
                    f"{diagnostics.eligible} eligible, "
                    f"{diagnostics.distinct_support_sets} distinct) ==="
                )
                if diagnostics.rejected:
                    print(f"    rejected: {diagnostics.rejected}")
                for index, selected in enumerate(composed.key_patterns, start=1):
                    pattern = selected.pattern
                    mark = "*" if pattern.kind == KIND_COMBINATION else " "
                    alternatives = (
                        f"  [also: {', '.join(selected.indistinguishable_from)}]"
                        if selected.has_alternatives
                        else ""
                    )
                    works = ", ".join(
                        f"{w.title[:24]} {w.rating}/10"
                        for w in pattern.supporting_works[: args.works]
                        if w.rating is not None
                    )
                    print(
                        f"  {index}.{mark}{pattern.key:42} {pattern.direction:8} "
                        f"n={pattern.works_rated} {'/'.join(pattern.domains)}"
                    )
                    print(f"       {works}{alternatives}")

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
