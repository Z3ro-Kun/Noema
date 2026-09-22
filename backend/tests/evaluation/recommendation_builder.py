"""Materialising the recommendation evaluation profiles into a session.

Everything goes through the ordinary `auth_service` and `library_service`
calls, exactly as `builder.py` does for the preference-engine cases: no row is
inserted directly, so the event trail and the preference engine see what they
would see from a real reader. A profile that produces no established
preference is therefore evidence about the engine rather than about the
fixture.

Flushes and never commits. The caller owns the transaction.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Work
from app.services import auth_service, library_service
from tests.evaluation.builder import MissingCorpusWorkError
from tests.evaluation.recommendation_dataset import (
    RECOMMENDATION_CASES,
    RECOMMENDATION_EMAIL_DOMAIN,
    RECOMMENDATION_PASSWORD,
    RecommendationCase,
    referenced_works,
)


@dataclass
class BuiltCase:
    spec: RecommendationCase
    user_id: uuid.UUID
    # Every work this profile rated, so a test can assert none of them comes
    # back as a recommendation without re-deriving the list.
    rated_work_ids: set[uuid.UUID]


async def resolve_works(session: AsyncSession) -> dict[tuple[str, str], uuid.UUID]:
    """Map every (source, source_ref) these profiles need to a real work id."""
    wanted = referenced_works()
    rows = (
        await session.execute(
            select(Work.id, Work.source, Work.external_ids["source_ref"].astext)
        )
    ).all()
    found = {(source, ref): work_id for work_id, source, ref in rows if source and ref}

    resolved = {}
    missing = []
    for key in sorted(wanted):
        if key in found:
            resolved[key] = found[key]
        else:
            missing.append(key)
    if missing:
        raise MissingCorpusWorkError(
            "the recommendation evaluation profiles reference works that are "
            "not ingested: " + ", ".join(f"{source}:{ref}" for source, ref in missing)
        )
    return resolved


async def build_case(
    session: AsyncSession,
    spec: RecommendationCase,
    works: dict[tuple[str, str], uuid.UUID],
) -> BuiltCase:
    user = await auth_service.register_user(
        session,
        email=spec.email,
        password=RECOMMENDATION_PASSWORD,
        display_name=spec.case,
    )

    rated: set[uuid.UUID] = set()
    for interaction in spec.interactions:
        work_id = works[(interaction.source, interaction.source_ref)]
        await library_service.add_to_library(session, user_id=user.id, work_id=work_id)
        for status in interaction.statuses:
            await library_service.set_status(
                session, user_id=user.id, work_id=work_id, status=status
            )
        if interaction.rating is not None:
            await library_service.set_rating(
                session, user_id=user.id, work_id=work_id, rating=interaction.rating
            )
        rated.add(work_id)

    await session.flush()
    return BuiltCase(spec=spec, user_id=user.id, rated_work_ids=rated)


async def build_recommendation_cases(session: AsyncSession) -> dict[str, BuiltCase]:
    """Build every case, keyed by its letter. Deterministic given the corpus."""
    works = await resolve_works(session)
    return {
        spec.case: await build_case(session, spec, works)
        for spec in RECOMMENDATION_CASES
    }


async def teardown_recommendation_cases(session: AsyncSession) -> int:
    """Remove every profile, matching on the reserved e-mail domain only."""
    users = (
        (
            await session.execute(
                select(User).where(User.email.like(f"%{RECOMMENDATION_EMAIL_DOMAIN}"))
            )
        )
        .scalars()
        .all()
    )
    for user in users:
        await session.delete(user)
    await session.flush()
    return len(users)
