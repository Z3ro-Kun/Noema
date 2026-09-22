"""Check the corpus against the manifest, and against itself.

    python -m scripts.validate_corpus

Phase 1AC. Two kinds of check, and the difference matters.

The first is "is the corpus what the manifest says": every domain at or above
its floor, every manifest entry actually ingested, nothing ingested twice.
A failure here is a seeding job that did not finish.

The second is structural, and does not care what the manifest says: no work
without a domain, no content unit without a container, no embedding pointing
at a unit that is gone, no duplicate work-concept edge, every vector at the
production dimension and normalized, every work with a source recorded. A
failure here is a bug.

Counts are reported, never asserted. How many containers a season has or how
many paragraphs a novel splits into is a property of the source, and a test
that pinned it would be a test of Dickens rather than of Noema.

Exit code is 0 when every check passes and 1 otherwise, so this can gate a
pipeline.
"""

import argparse
import asyncio

from sqlalchemy import func, select

from app.core.db import async_session_factory, engine
from app.models import (
    Concept,
    Container,
    ContentUnit,
    Domain,
    Embedding,
    Work,
    WorkConcept,
)
from scripts.corpus_manifest import PLAN, MINIMUM_PER_DOMAIN, source_refs

# The production embedding contract. Changing either of these is a migration,
# not a configuration change.
EXPECTED_DIMENSION = 768
EXPECTED_MODEL = "sentence-transformers/all-mpnet-base-v2"

# A content unit hangs off a container or off a work, never both. Everything
# that counts units per work resolves that the same way, so the two kinds are
# counted together and neither is silently dropped by an inner join.
UNIT_WORK_ID = func.coalesce(Container.work_id, ContentUnit.work_id)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-embeddings",
        action="store_true",
        help="report structure only, for use while the embedding pass is still running",
    )
    return parser.parse_args(argv)


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, ok: bool, label: str, detail: str = "") -> None:
        print(f"  [{'ok' if ok else 'FAIL'}] {label}{f' -- {detail}' if detail else ''}")
        if not ok:
            self.failures.append(label)


async def run(args: argparse.Namespace) -> int:
    report = Report()

    async with async_session_factory() as session:
        scalar = lambda stmt: session.execute(stmt)  # noqa: E731

        # --- counts, reported ------------------------------------------
        print("\n=== corpus ===")
        rows = (
            await session.execute(
                select(
                    Domain.slug,
                    func.count(func.distinct(Work.id)),
                    func.count(func.distinct(Container.id)),
                    func.count(func.distinct(ContentUnit.id)),
                )
                .select_from(Domain)
                .outerjoin(Work, Work.domain_id == Domain.id)
                .outerjoin(Container, Container.work_id == Work.id)
                .outerjoin(
                    ContentUnit,
                    (ContentUnit.container_id == Container.id)
                    | (ContentUnit.work_id == Work.id),
                )
                .group_by(Domain.slug)
                .order_by(Domain.slug)
            )
        ).all()

        print(f"  {'domain':12} {'works':>6} {'containers':>11} {'units':>8} {'embedded':>9}")
        total_works = 0
        for slug, works, containers, units in rows:
            embedded = (
                await session.execute(
                    select(func.count())
                    .select_from(Embedding)
                    .join(ContentUnit, ContentUnit.id == Embedding.owner_id)
                    .outerjoin(Container, Container.id == ContentUnit.container_id)
                    .join(Work, Work.id == UNIT_WORK_ID)
                    .join(Domain, Domain.id == Work.domain_id)
                    .where(Embedding.owner_type == "content_unit", Domain.slug == slug)
                )
            ).scalar_one()
            total_works += works
            print(f"  {slug:12} {works:6} {containers:11} {units:8} {embedded:9}")

        # Reported, never checked: how many works semantic search can actually
        # reach. A work with no text is catalogued but invisible, and the gap
        # between the two numbers is the corpus limitation worth watching.
        print(f"\n  {'domain':12} {'works':>6} {'searchable':>11} {'metadata-only':>14}")
        for slug, works, _containers, _units in rows:
            searchable = (
                await session.execute(
                    select(func.count(func.distinct(Work.id)))
                    .select_from(Work)
                    .join(Domain, Domain.id == Work.domain_id)
                    .outerjoin(Container, Container.work_id == Work.id)
                    .join(
                        ContentUnit,
                        (ContentUnit.container_id == Container.id)
                        | (ContentUnit.work_id == Work.id),
                    )
                    .where(
                        Domain.slug == slug,
                        ContentUnit.text_content.is_not(None),
                        ContentUnit.text_content != "",
                    )
                )
            ).scalar_one()
            print(f"  {slug:12} {works:6} {searchable:11} {works - searchable:14}")

        work_level_units = (
            await session.execute(
                select(func.count())
                .select_from(ContentUnit)
                .where(ContentUnit.work_id.is_not(None))
            )
        ).scalar_one()
        print(f"\n  work-level summary units: {work_level_units}")

        concepts = (await scalar(select(func.count()).select_from(Concept))).scalar_one()
        work_concepts = (
            await scalar(select(func.count()).select_from(WorkConcept))
        ).scalar_one()
        embeddings = (await scalar(select(func.count()).select_from(Embedding))).scalar_one()
        sources = (
            await scalar(select(func.count(func.distinct(Work.source))))
        ).scalar_one()
        print(
            f"\n  total works {total_works} | concepts {concepts} | "
            f"work-concepts {work_concepts} | embeddings {embeddings} | "
            f"distinct sources {sources}"
        )

        # --- against the manifest --------------------------------------
        print("\n=== manifest ===")
        for plan in PLAN:
            works = (
                await session.execute(
                    select(func.count())
                    .select_from(Work)
                    .join(Domain, Domain.id == Work.domain_id)
                    .where(Domain.slug == plan.slug)
                )
            ).scalar_one()
            report.check(
                works >= MINIMUM_PER_DOMAIN,
                f"{plan.slug} has at least {MINIMUM_PER_DOMAIN} works",
                f"{works}",
            )

            stored = {
                ref
                for (ref,) in (
                    await session.execute(
                        select(Work.external_ids["source_ref"].astext)
                        .join(Domain, Domain.id == Work.domain_id)
                        .where(Domain.slug == plan.slug)
                    )
                ).all()
            }
            missing = source_refs(plan.slug) - stored
            report.check(
                not missing,
                f"{plan.slug} has every manifest entry",
                f"missing {sorted(missing)}" if missing else "",
            )

        report.check(total_works >= 60, "at least 60 works in total", f"{total_works}")

        # --- structure, regardless of the manifest ---------------------
        print("\n=== integrity ===")
        # Through a subquery: Postgres will not group by a parameterised JSON
        # extraction, because the expression in GROUP BY is not textually the
        # one in SELECT.
        identity = (
            select(
                Work.source.label("source"),
                Work.external_ids["source_ref"].astext.label("ref"),
            )
            .subquery()
        )
        duplicates = (
            await session.execute(
                select(identity.c.source, identity.c.ref, func.count())
                .group_by(identity.c.source, identity.c.ref)
                .having(func.count() > 1)
            )
        ).all()
        report.check(not duplicates, "no duplicate canonical works", str(duplicates or ""))

        orphan_works = (
            await session.execute(
                select(func.count())
                .select_from(Work)
                .outerjoin(Domain, Domain.id == Work.domain_id)
                .where(Domain.id.is_(None))
            )
        ).scalar_one()
        report.check(orphan_works == 0, "every work has a domain", f"{orphan_works} without")

        no_source = (
            await session.execute(
                select(func.count()).select_from(Work).where(Work.source.is_(None))
            )
        ).scalar_one()
        report.check(no_source == 0, "every work records its source", f"{no_source} without")

        orphan_units = (
            await session.execute(
                select(func.count())
                .select_from(ContentUnit)
                .outerjoin(Container, Container.id == ContentUnit.container_id)
                .outerjoin(Work, Work.id == ContentUnit.work_id)
                .where(Container.id.is_(None), Work.id.is_(None))
            )
        ).scalar_one()
        report.check(orphan_units == 0, "no orphaned content units", f"{orphan_units}")

        # The database constrains this, so a non-zero count here means the
        # constraint is missing rather than that a row slipped past it.
        two_parents = (
            await session.execute(
                select(func.count())
                .select_from(ContentUnit)
                .where(
                    ContentUnit.container_id.is_not(None),
                    ContentUnit.work_id.is_not(None),
                )
            )
        ).scalar_one()
        report.check(
            two_parents == 0, "every content unit has exactly one parent", f"{two_parents} with two"
        )

        orphan_containers = (
            await session.execute(
                select(func.count())
                .select_from(Container)
                .outerjoin(Work, Work.id == Container.work_id)
                .where(Work.id.is_(None))
            )
        ).scalar_one()
        report.check(orphan_containers == 0, "no orphaned containers", f"{orphan_containers}")

        orphan_embeddings = (
            await session.execute(
                select(func.count())
                .select_from(Embedding)
                .outerjoin(ContentUnit, ContentUnit.id == Embedding.owner_id)
                .where(Embedding.owner_type == "content_unit", ContentUnit.id.is_(None))
            )
        ).scalar_one()
        report.check(orphan_embeddings == 0, "no orphaned embeddings", f"{orphan_embeddings}")

        bad_edges = (
            await session.execute(
                select(func.count())
                .select_from(WorkConcept)
                .outerjoin(Work, Work.id == WorkConcept.work_id)
                .outerjoin(Concept, Concept.id == WorkConcept.concept_id)
                .where((Work.id.is_(None)) | (Concept.id.is_(None)))
            )
        ).scalar_one()
        report.check(bad_edges == 0, "no invalid work-concept edges", f"{bad_edges}")

        duplicate_edges = (
            await session.execute(
                select(WorkConcept.work_id, WorkConcept.concept_id, func.count())
                .group_by(WorkConcept.work_id, WorkConcept.concept_id)
                .having(func.count() > 1)
            )
        ).all()
        report.check(
            not duplicate_edges,
            "no duplicate work-concept edges",
            f"{len(duplicate_edges)} duplicated" if duplicate_edges else "",
        )

        # --- the embedding contract ------------------------------------
        print("\n=== embeddings ===")
        if args.skip_embeddings:
            print("  (skipped)")
        else:
            dimensions = {
                dimension
                for (dimension,) in (
                    await session.execute(select(Embedding.dimension).distinct())
                ).all()
            }
            report.check(
                dimensions <= {EXPECTED_DIMENSION},
                f"every vector is {EXPECTED_DIMENSION}-dimensional",
                f"found {sorted(dimensions)}",
            )

            models = {
                model
                for (model,) in (
                    await session.execute(select(Embedding.model_name).distinct())
                ).all()
            }
            report.check(
                EXPECTED_MODEL in models,
                "the production model is present",
                f"found {sorted(models)}",
            )

            unnormalized = (
                await session.execute(
                    select(func.count())
                    .select_from(Embedding)
                    .where(Embedding.normalized.is_(False))
                )
            ).scalar_one()
            report.check(unnormalized == 0, "every vector is normalized", f"{unnormalized} not")

            unembedded = (
                await session.execute(
                    select(func.count())
                    .select_from(ContentUnit)
                    .outerjoin(
                        Embedding,
                        (Embedding.owner_id == ContentUnit.id)
                        & (Embedding.owner_type == "content_unit"),
                    )
                    .where(
                        ContentUnit.text_content.is_not(None),
                        Embedding.id.is_(None),
                    )
                )
            ).scalar_one()
            report.check(
                unembedded == 0,
                "every content unit with text has an embedding",
                f"{unembedded} awaiting",
            )

    print()
    if report.failures:
        print(f"{len(report.failures)} check(s) FAILED: {', '.join(report.failures)}")
        return 1
    print("all checks passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    async def _run() -> int:
        try:
            return await run(parse_args(argv))
        finally:
            await engine.dispose()

    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
