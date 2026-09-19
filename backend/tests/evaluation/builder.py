"""Materialising the evaluation library into a session.

Everything goes through the ordinary `auth_service` and `library_service`
calls the product itself uses. Nothing is inserted directly. That matters for
two reasons: the `UserContentEvent` trail is produced the same way a real
user's would be, and the fixtures exercise the same validation, so a dataset
that builds is a dataset the API would have accepted.

The builder flushes and never commits. The caller owns the transaction, which
lets tests roll the whole thing back and lets the CLI commit deliberately.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Work
from app.services import auth_service, library_service
from tests.evaluation.dataset import (
    EVALUATION_PASSWORD,
    EVALUATION_USERS,
    EvaluationUser,
    referenced_works,
)


class MissingCorpusWorkError(LookupError):
    """A work the dataset references is not ingested.

    Raised rather than skipped: a fixture that quietly builds a smaller,
    different dataset would invalidate every expectation documented against
    it without anyone noticing.
    """


@dataclass
class BuiltUser:
    spec: EvaluationUser
    user_id: uuid.UUID
    work_ids: dict[tuple[str, str], uuid.UUID]


async def resolve_works(session: AsyncSession) -> dict[tuple[str, str], uuid.UUID]:
    """Map every (source, source_ref) the dataset needs to a real work id."""
    wanted = referenced_works()
    rows = (
        (
            await session.execute(
                select(Work.id, Work.source, Work.external_ids["source_ref"].astext)
            )
        )
        .all()
    )
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
            "the evaluation dataset references works that are not ingested: "
            + ", ".join(f"{source}:{ref}" for source, ref in missing)
        )
    return resolved


async def build_user(
    session: AsyncSession,
    spec: EvaluationUser,
    works: dict[tuple[str, str], uuid.UUID],
) -> BuiltUser:
    """Create one evaluation user and replay their history."""
    user = await auth_service.register_user(
        session, email=spec.email, password=EVALUATION_PASSWORD, display_name=spec.case
    )

    for interaction in spec.interactions:
        work_id = works[(interaction.source, interaction.source_ref)]
        await library_service.add_to_library(session, user_id=user.id, work_id=work_id)

        # Applied in order, through the real service, so each transition
        # appends its own event and the reconsumption counters move honestly.
        for status in interaction.statuses:
            await library_service.set_status(
                session, user_id=user.id, work_id=work_id, status=status
            )

        if interaction.rating is not None:
            await library_service.set_rating(
                session, user_id=user.id, work_id=work_id, rating=interaction.rating
            )

        if interaction.removed:
            await library_service.remove_from_library(
                session, user_id=user.id, work_id=work_id
            )

    await session.flush()
    return BuiltUser(spec=spec, user_id=user.id, work_ids=works)


async def build_evaluation_library(session: AsyncSession) -> list[BuiltUser]:
    """Build every documented case. Deterministic given the same corpus."""
    works = await resolve_works(session)
    return [await build_user(session, spec, works) for spec in EVALUATION_USERS]


async def teardown_evaluation_library(session: AsyncSession) -> int:
    """Remove every evaluation user and everything hanging off them.

    Matches on the reserved e-mail domain, so it cannot reach a real account.
    Interactions and events cascade from the user; canonical content is never
    touched, because the dataset only ever referenced it.
    """
    from tests.evaluation.dataset import EVALUATION_EMAIL_DOMAIN

    users = (
        (
            await session.execute(
                select(User).where(User.email.like(f"%{EVALUATION_EMAIL_DOMAIN}"))
            )
        )
        .scalars()
        .all()
    )
    for user in users:
        await session.delete(user)
    await session.flush()
    return len(users)
