"""Phase 1U: the structured insights each evaluation case produces.

    python -m scripts.run_profile_insights
    python -m scripts.run_profile_insights --json data/evaluation/profile_insights.json

An **evaluation artifact, not a product surface.** It builds the evaluation
library inside a transaction, derives each case's insights, prints them and
rolls the whole thing back. Nothing is committed and no user data survives.

The emitted structure is what a renderer would receive minus one field: the
normalized rating is dropped here, as Phase 1P's product contract drops it,
so the artifact shows the shape a user-facing DTO would actually carry rather
than the inspection form the service holds.

Nothing in this file turns an insight into a sentence. That is the point of
the phase -- the keys are printed as keys.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.core.db import async_session_factory, engine
from app.services.preference.insights import build_profile_insights
from tests.evaluation.builder import MissingCorpusWorkError, build_evaluation_library


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=None, help="write the result here")
    parser.add_argument("--works", type=int, default=4, help="works printed per insight")
    return parser.parse_args(argv)


def insight_row(insight) -> dict:
    evidence = insight.evidence
    row = {
        "observation": insight.observation,
        "presentation_key": insight.presentation_key,
        "features": [
            {"key": f.key, "name": f.name, "family": f.family} for f in insight.features
        ],
        "pattern_keys": list(insight.pattern_keys),
        "indistinguishable_from": list(insight.indistinguishable_from),
        "evidence": None,
    }
    if evidence is not None:
        row["evidence"] = {
            "direction": evidence.direction,
            "status": evidence.status,
            "confidence": evidence.confidence,
            "preference_evidence": evidence.preference_evidence,
            "works_rated": evidence.works_rated,
            "domains": list(evidence.domains),
            "domain_count": evidence.domain_count,
            "is_cross_domain": evidence.is_cross_domain,
            "works_reconsumed": evidence.works_reconsumed,
            "total_completions": evidence.total_completions,
            "works_abandoned": evidence.works_abandoned,
            "works_on_hold": evidence.works_on_hold,
            "reconsumption_signal": evidence.reconsumption_signal,
            "abandonment_signal": evidence.abandonment_signal,
            "constituent_evidence": list(evidence.constituent_evidence),
            "supporting_works": [
                {
                    "title": w.title,
                    "domain": w.domain,
                    "rating": w.rating,
                    "times_completed": w.times_completed,
                    "in_library": w.in_library,
                }
                for w in evidence.supporting_works
            ],
        }
    return row


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
                result = await build_profile_insights(session, entry.user_id)
                diagnostics = result.diagnostics
                report["cases"][entry.spec.case] = {
                    "expectation": entry.spec.expectation,
                    "insights": [insight_row(i) for i in result.insights],
                    "diagnostics": {
                        "key_patterns": diagnostics.key_patterns,
                        "early_signals": diagnostics.early_signals,
                        "pattern_insights": diagnostics.pattern_insights,
                        "emerging_insights": diagnostics.emerging_insights,
                        "profile_insights": diagnostics.profile_insights,
                        "pattern_insights_capped": diagnostics.pattern_insights_capped,
                        "emerging_insights_capped": (
                            diagnostics.emerging_insights_capped
                        ),
                    },
                }

                print(
                    f"\n=== case {entry.spec.case}: {len(result.insights)} insights "
                    f"({diagnostics.pattern_insights} pattern, "
                    f"{diagnostics.profile_insights} profile, "
                    f"{diagnostics.emerging_insights} emerging) "
                    f"from {diagnostics.key_patterns} key patterns ==="
                )
                for insight in result.insights:
                    features = " + ".join(f.key for f in insight.features) or "--"
                    evidence = insight.evidence
                    print(f"  {insight.observation:22} {insight.presentation_key:24} {features}")
                    if evidence is None:
                        print(f"       over: {', '.join(insight.pattern_keys)}")
                        continue
                    works = ", ".join(
                        f"{w.title[:24]} {w.rating}/10"
                        for w in evidence.supporting_works[: args.works]
                        if w.rating is not None
                    )
                    behaviour = ""
                    if evidence.works_reconsumed:
                        behaviour = (
                            f"  [returned to {evidence.works_reconsumed}, "
                            f"{evidence.total_completions} completions]"
                        )
                    print(
                        f"       {evidence.direction}/{evidence.status}, "
                        f"{evidence.works_rated} rated, "
                        f"{'/'.join(evidence.domains)}{behaviour}"
                    )
                    print(f"       {works}")
                    if insight.indistinguishable_from:
                        print(
                            "       indistinguishable from: "
                            f"{', '.join(insight.indistinguishable_from)}"
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
