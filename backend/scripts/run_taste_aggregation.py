"""Phase 1S: what taste aggregation finds in the evaluation library.

    python -m scripts.run_taste_aggregation
    python -m scripts.run_taste_aggregation --json data/evaluation/taste_aggregation.json
    python -m scripts.run_taste_aggregation --sweep

An **evaluation artifact, not a product surface.** It builds the evaluation
library inside a transaction, aggregates every case, prints what a reader of
the structure would see, and rolls the whole thing back. Nothing is committed
and no user data survives the run.

`--sweep` re-runs the whole set across a range of support minimums and prints
how many patterns survive each, so the thresholds in `TasteParameters` can be
judged against their neighbours rather than taken on trust.
"""

import argparse
import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

from app.core.db import async_session_factory, engine
from app.services.preference.taste import (
    DEFAULT_TASTE_PARAMETERS,
    KIND_COMBINATION,
    STATUS_ESTABLISHED,
    TasteParameters,
    build_taste_profile,
)
from tests.evaluation.builder import MissingCorpusWorkError, build_evaluation_library


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=None, help="write the full result here")
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="report pattern counts across neighbouring support thresholds",
    )
    parser.add_argument("--top", type=int, default=8, help="patterns printed per case")
    return parser.parse_args(argv)


def pattern_row(pattern) -> dict:
    return {
        "pattern": pattern.key,
        "features": [
            {"key": f.key, "name": f.name, "family": f.family} for f in pattern.features
        ],
        "kind": pattern.kind,
        "status": pattern.status,
        "direction": pattern.direction,
        "preference_evidence": pattern.preference_evidence,
        "confidence": pattern.confidence,
        "works_rated": pattern.works_rated,
        "works_exposed": pattern.works_exposed,
        "works_completed": pattern.works_completed,
        "works_reconsumed": pattern.works_reconsumed,
        "total_completions": pattern.total_completions,
        "works_abandoned": pattern.works_abandoned,
        "works_on_hold": pattern.works_on_hold,
        "reconsumption_signal": pattern.reconsumption_signal,
        "abandonment_signal": pattern.abandonment_signal,
        "domains": list(pattern.domains),
        "constituent_evidence": list(pattern.constituent_evidence),
        "shares_support_with": list(pattern.shares_support_with),
        "supporting_works": [
            {
                "title": w.title,
                "domain": w.domain_slug,
                "rating": w.rating,
                "status": w.status,
                "times_completed": w.times_completed,
                "in_library": w.in_library,
            }
            for w in pattern.supporting_works
        ],
    }


async def sweep(session, built) -> dict:
    """Pattern counts either side of the chosen minimums."""
    out: dict = {}
    for individual_minimum in (2, 3):
        for combination_minimum in (2, 3, 4):
            parameters = TasteParameters(
                minimum_individual_rated=individual_minimum,
                minimum_combination_rated=combination_minimum,
            )
            totals = {"individual": 0, "combination": 0, "established": 0}
            for entry in built:
                profile = await build_taste_profile(session, entry.user_id, taste=parameters)
                totals["individual"] += len(profile.individual())
                totals["combination"] += len(profile.combinations())
                totals["established"] += len(profile.established())
            out[f"individual>={individual_minimum},combination>={combination_minimum}"] = totals
    return out


async def run(args: argparse.Namespace) -> int:
    report: dict = {
        "parameters": {
            "minimum_individual_rated": DEFAULT_TASTE_PARAMETERS.minimum_individual_rated,
            "minimum_combination_rated": DEFAULT_TASTE_PARAMETERS.minimum_combination_rated,
            "combination_distinction": DEFAULT_TASTE_PARAMETERS.combination_distinction(),
        },
        "cases": {},
    }

    try:
        async with async_session_factory() as session:
            try:
                built = await build_evaluation_library(session)
            except MissingCorpusWorkError as exc:
                print(str(exc), file=sys.stderr)
                return 1

            for entry in built:
                profile = await build_taste_profile(session, entry.user_id)
                diagnostics = profile.diagnostics
                report["cases"][entry.spec.case] = {
                    "expectation": entry.spec.expectation,
                    "patterns": [pattern_row(p) for p in profile.patterns],
                    "diagnostics": {
                        "features_available": diagnostics.features_available,
                        "individual_candidates": diagnostics.individual_candidates,
                        "individual_below_support": diagnostics.individual_below_support,
                        "pairs_considered": diagnostics.pairs_considered,
                        "pairs_rejected": diagnostics.pairs_rejected,
                        "pairs_admitted": diagnostics.pairs_admitted,
                        "ambiguous_support_sets": diagnostics.ambiguous_support_sets,
                    },
                }

                established = [p for p in profile.patterns if p.status == STATUS_ESTABLISHED]
                print(
                    f"\n=== case {entry.spec.case}: {len(profile.patterns)} patterns "
                    f"({len(profile.individual())} individual, "
                    f"{len(profile.combinations())} combination), "
                    f"{len(established)} established ==="
                )
                print(
                    f"    considered {diagnostics.pairs_considered} pairs, "
                    f"admitted {diagnostics.pairs_admitted}; "
                    f"rejected {diagnostics.pairs_rejected}"
                )
                for pattern in profile.patterns[: args.top]:
                    mark = "*" if pattern.kind == KIND_COMBINATION else " "
                    ambiguous = (
                        f" [shares support with {len(pattern.shares_support_with)}]"
                        if pattern.is_ambiguous
                        else ""
                    )
                    print(
                        f"  {mark} {pattern.key:46} {pattern.status:12} "
                        f"{pattern.direction:8} ev={pattern.preference_evidence:+.3f} "
                        f"conf={pattern.confidence:.3f} n={pattern.works_rated} "
                        f"{'/'.join(pattern.domains)}{ambiguous}"
                    )

            if args.sweep:
                report["sweep"] = await sweep(session, built)
                print("\n=== threshold sweep (totals across all cases) ===")
                for label, totals in report["sweep"].items():
                    print(
                        f"  {label:38} individual={totals['individual']:4} "
                        f"combination={totals['combination']:4} "
                        f"established={totals['established']:4}"
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
