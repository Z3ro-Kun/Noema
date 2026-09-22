"""Does the recommendation ranking read the reader, or the tagging?

    python -m scripts.analyze_concept_bias

Diagnostic only. It runs the production recommender over controlled reader
profiles, measures each candidate's ranking against how many concepts the
catalogue happens to have attached to it, and prints the result. It changes
no score and writes nothing.

The profiles come from the evaluation library, so they are built through the
ordinary library and rating calls rather than injected -- the preference
engine does the same work here it does for a real reader. Everything is
rolled back at the end.

Read the two correlations together, not separately:

    concepts_vs_score   ordering by total concept count against ordering by
                        score. High on its own means little: a work with more
                        concepts genuinely can match more preferences.

    matched_vs_score    the same for concepts the reader's own preferences
                        are about.

If the first approaches the second, the ranking is being driven by how
talkative the catalogue is about a work rather than by the reader's taste,
and a normalization experiment has a case. If the second is clearly stronger,
it does not.
"""

import argparse
import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.services.recommendation_diagnostics import (
    corpus_concept_distribution,
    diagnose,
)

# Cases chosen for spread rather than for outcome: a broad profile, a narrow
# one, one with negatives, and one whose evidence sits in a single medium.
PROFILE_CASES = ("A", "G", "K", "P", "R", "S", "U")

TOP = 8


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=TOP, help="how many ranks to inspect")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    try:
        async with factory() as session:
            print("\n=== corpus: concepts per work, by domain ===")
            print(
                f"  {'domain':12} {'works':>6} {'mean':>7} {'median':>7} "
                f"{'min':>5} {'max':>5}"
            )
            distribution = await corpus_concept_distribution(session)
            for summary in distribution.values():
                print(
                    f"  {summary.domain_slug:12} {summary.works:6} "
                    f"{summary.mean_concepts:7.2f} {summary.median_concepts:7.1f} "
                    f"{summary.min_concepts:5} {summary.max_concepts:5}"
                )

            from tests.evaluation.builder import build_evaluation_library

            built = await build_evaluation_library(session)
            by_case = {entry.spec.case: entry for entry in built}

            rows: list[tuple[str, float | None, float | None]] = []
            reports: list[tuple[str, object]] = []
            for case in PROFILE_CASES:
                entry = by_case.get(case)
                if entry is None:
                    continue
                report = await diagnose(session, entry.user_id, label=f"case {case}")
                print(f"\n=== {report.label}: state={report.state} ===")
                if not report.candidates:
                    print("  no scored candidates")
                    continue

                print(
                    f"  candidates {len(report.candidates)} | "
                    f"mean concepts overall {report.mean_concepts_overall():.2f} | "
                    f"in top {args.top} {report.mean_concepts_in_top(args.top):.2f}"
                )
                print(
                    f"  spearman: concepts~score {report.concepts_vs_score} | "
                    f"matched~score {report.matched_vs_score} | "
                    f"concepts~rank {report.concepts_vs_rank} | "
                    f"matched~rank {report.matched_vs_rank}"
                )
                print(f"  domains in top {args.top}: {report.domain_share_in_top(args.top)}")
                print(
                    f"\n  {'#':>3} {'title':34} {'domain':11} {'tot':>4} "
                    f"{'match':>6} {'acc':>4} {'pos':>7} {'neg':>7} {'score':>7}"
                )
                for item in report.top(args.top):
                    print(
                        f"  {item.rank:3} {item.title[:32]:34} {item.domain_slug:11} "
                        f"{item.total_concepts:4} {item.matched_concepts:6} "
                        f"{item.accepted_contributions:4} {item.positive_total:7.3f} "
                        f"{item.negative_total:7.3f} {item.score:7.3f}"
                    )
                rows.append((report.label, report.concepts_vs_score, report.matched_vs_score))
                reports.append((report.label, report))

            print("\n=== across profiles ===")
            print(f"  {'profile':12} {'concepts~score':>15} {'matched~score':>15}")
            for label, concepts, matched in rows:
                print(f"  {label:12} {str(concepts):>15} {str(matched):>15}")

            usable = [(c, m) for _, c, m in rows if c is not None and m is not None]
            if usable:
                print(
                    f"\n  mean concepts~score {sum(c for c, _ in usable) / len(usable):.4f}"
                    f" | mean matched~score {sum(m for _, m in usable) / len(usable):.4f}"
                )

            print("\n=== where the best literature candidate landed ===")
            print(
                f"  {'profile':12} {'rank':>6} {'of':>5} {'title':32} "
                f"{'tot':>4} {'match':>6} {'best match in shelf':>20}"
            )
            for label, report in reports:
                literature = [
                    item for item in report.candidates if item.domain_slug == "literature"
                ]
                if not literature:
                    print(f"  {label:12} {'none':>6}")
                    continue
                best = literature[0]
                ceiling = max(item.matched_concepts for item in report.candidates)
                print(
                    f"  {label:12} {best.rank:6} {len(report.candidates):5} "
                    f"{best.title[:30]:32} {best.total_concepts:4} "
                    f"{best.matched_concepts:6} {ceiling:20}"
                )

            await session.rollback()
    finally:
        await engine.dispose()

    return 0


def main() -> int:
    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
