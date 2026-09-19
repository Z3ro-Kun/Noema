"""Assembling the user-facing view of canonical works.

Reads existing canonical data and nothing else. This module adds no column,
stores nothing, and never writes: the product surface is a *projection* of
the corpus, so a presentation requirement can never become a reason to
change a Work row.

Two things it decides, both of which are genuine editorial judgements rather
than plumbing:

  Which credits to show   A work carries up to 33, most of them translators,
                          letterers and per-episode animators. An allowlist
                          of product-facing roles keeps the noise out.
  What counts as synopsis The source's own description, or nothing. Never
                          the opening of the work's own text.

Loading is eager and batched. The obvious shape here is one query per work
for creators and another for concepts, which is an N+1 waiting to happen on
a library listing; every function below loads relationships for the whole
set at once.
"""

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Concept,
    Creator,
    Domain,
    UserContentInteraction,
    Work,
    WorkConcept,
    WorkCreator,
)
from app.schemas.product import (
    ProductConcept,
    ProductCreator,
    ProductDomain,
    ProductWork,
    UserWorkState,
    WorkPresentation,
)

# Credits worth putting in front of a reader, in display order. A closed
# allowlist for the same reason the concept vocabulary is one: the corpus
# holds 57 distinct base roles, and a blocklist would need extending every
# time a source invented another.
#
# Maps the source's base role to the label shown. Anything absent here is not
# shown -- Key Animation, Storyboard, Script, ADR Director, Producer, Theme
# Song Performance, production_company and the rest are real credits that
# belong on a credits page, not on a library card.
PRODUCT_ROLES: tuple[tuple[str, str], ...] = (
    ("author", "Author"),
    ("Story & Art", "Story & Art"),
    ("Original Creator", "Original Creator"),
    ("Original Story", "Original Story"),
    ("Story", "Story"),
    ("Art", "Art"),
    ("Director", "Director"),
    ("Series Composition", "Series Composition"),
    ("studio", "Studio"),
)
_ROLE_LABELS = dict(PRODUCT_ROLES)
_ROLE_ORDER = {base: index for index, (base, _) in enumerate(PRODUCT_ROLES)}

# A qualifier naming a language marks a localisation credit: "Director
# (English; Netflix)" directed an English dub, not the work. "Story (chs
# 1-92)" is a genuine creative credit, so qualifiers are not excluded
# wholesale -- only those naming a language.
LOCALISATION_LANGUAGES = frozenset(
    {
        "english",
        "japanese",
        "french",
        "german",
        "italian",
        "spanish",
        "portuguese",
        "brazilian portuguese",
        "polish",
        "chinese",
        "korean",
        "russian",
        "dutch",
        "hungarian",
        "arabic",
        "turkish",
        "vietnamese",
        "thai",
        "indonesian",
        "hebrew",
    }
)


def _base_role(role: str) -> str:
    """The role without its parenthetical qualifier."""
    return role.split("(")[0].strip()


def _is_localisation(role: str) -> bool:
    if "(" not in role:
        return False
    qualifier = role.split("(", 1)[1].rstrip(")").strip().lower()
    # "English; Netflix" and "English, Viz Media" both name a language first.
    parts = [part.strip() for chunk in qualifier.split(";") for part in chunk.split(",")]
    return any(part in LOCALISATION_LANGUAGES for part in parts)


def select_product_creators(credits: list[tuple[str, str]]) -> list[ProductCreator]:
    """Pick the credits worth showing, from (role, name) pairs.

    Ordered by role importance rather than by name, so the person a reader
    is looking for comes first. Returns an empty list when a work has no
    product-facing credit at all, which is a real case -- a one-episode
    special with no director or studio credited -- and is left empty rather
    than filled with whatever else was on hand.
    """
    chosen: list[tuple[int, str, str]] = []
    seen: set[tuple[str, str]] = set()

    for role, name in credits:
        if not role or not name or _is_localisation(role):
            continue
        base = _base_role(role)
        label = _ROLE_LABELS.get(base)
        if label is None:
            continue
        if (label, name) in seen:
            continue
        seen.add((label, name))
        chosen.append((_ROLE_ORDER[base], label, name))

    chosen.sort(key=lambda item: (item[0], item[2]))
    return [ProductCreator(name=name, role=label) for _, label, name in chosen]


def _anilist(work: Work) -> dict:
    return ((work.extra_metadata or {}).get("anilist")) or {}


def work_genres(work: Work) -> list[str]:
    """The source's own genre labels. Empty when the source states none."""
    return [genre for genre in (_anilist(work).get("genres") or []) if genre]


def work_year(work: Work) -> int | None:
    """Publication/broadcast year, where the source recorded a start date."""
    anilist = _anilist(work)
    start = anilist.get("start_date")
    if isinstance(start, str) and start[:4].isdigit():
        return int(start[:4])
    year = anilist.get("season_year")
    return year if isinstance(year, int) else None


def work_media_format(work: Work) -> str | None:
    """The source's format label: "TV", "MOVIE", "MANGA"."""
    value = _anilist(work).get("format")
    return value if isinstance(value, str) else None


def work_synopsis(work: Work) -> str | None:
    """The source's own description, or nothing.

    `Work.description` is populated by the AniList adapters from the
    catalogue's synopsis field. The literature adapter records no
    description, so literature works return None -- an honest absence.

    Deliberately never falls back to the work's own text. The opening
    paragraphs of a novel are corpus content, not a synopsis, and presenting
    them as one would put primary text on the product surface.
    """
    description = (work.description or "").strip()
    return description or None


def work_cover_image_url(work: Work) -> str | None:
    """Cover art URL, where a source supplied one.

    Null for every work in the current corpus: neither AniList query requests
    `coverImage` and Gutenberg's catalogue records carry none, so no cover
    data was ever ingested. Returning null is the whole implementation --
    guessing a CDN URL pattern would be fabricating one.
    """
    for key in ("cover_image_url", "cover_image"):
        value = (work.extra_metadata or {}).get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def _creators_for(
    session: AsyncSession, work_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[ProductCreator]]:
    """Credits for many works in one query."""
    if not work_ids:
        return {}
    rows = await session.execute(
        select(WorkCreator.work_id, WorkCreator.role, Creator.name)
        .join(Creator, WorkCreator.creator_id == Creator.id)
        .where(WorkCreator.work_id.in_(work_ids))
    )
    grouped: dict[uuid.UUID, list[tuple[str, str]]] = defaultdict(list)
    for work_id, role, name in rows.all():
        grouped[work_id].append((role, name))
    return {work_id: select_product_creators(credits) for work_id, credits in grouped.items()}


async def _concepts_for(
    session: AsyncSession, work_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[ProductConcept]]:
    """Concepts for many works in one query, strongest stated relevance first."""
    if not work_ids:
        return {}
    rows = await session.execute(
        select(WorkConcept.work_id, Concept.slug, Concept.name, Concept.concept_type)
        .join(Concept, WorkConcept.concept_id == Concept.id)
        .where(WorkConcept.work_id.in_(work_ids))
        .order_by(WorkConcept.confidence.desc().nullslast(), Concept.name)
    )
    grouped: dict[uuid.UUID, list[ProductConcept]] = defaultdict(list)
    for work_id, slug, name, concept_type in rows.all():
        grouped[work_id].append(
            ProductConcept(slug=slug, name=name, concept_type=concept_type)
        )
    return grouped


def build_product_work(
    work: Work,
    *,
    creators: list[ProductCreator],
    concepts: list[ProductConcept],
) -> ProductWork:
    return ProductWork(
        id=work.id,
        title=work.title,
        original_title=work.original_title,
        domain=ProductDomain(slug=work.domain.slug, name=work.domain.name),
        synopsis=work_synopsis(work),
        cover_image_url=work_cover_image_url(work),
        genres=work_genres(work),
        concepts=concepts,
        creators=creators,
        media_format=work_media_format(work),
        year=work_year(work),
        source=work.source,
    )


def build_user_state(interaction: UserContentInteraction | None) -> UserWorkState | None:
    if interaction is None:
        return None
    return UserWorkState(
        status=interaction.status,
        rating=interaction.rating,
        rated_at=interaction.rated_at,
        added_at=interaction.added_at,
        started_at=interaction.started_at,
        completed_at=interaction.completed_at,
        abandoned_at=interaction.abandoned_at,
        removed_at=interaction.removed_at,
        times_started=interaction.times_started,
        times_completed=interaction.times_completed,
        in_library=interaction.removed_at is None,
    )


async def product_works_for(
    session: AsyncSession, works: list[Work]
) -> dict[uuid.UUID, ProductWork]:
    """Project many works at once: three queries total, never one per work."""
    work_ids = [work.id for work in works]
    creators = await _creators_for(session, work_ids)
    concepts = await _concepts_for(session, work_ids)
    return {
        work.id: build_product_work(
            work, creators=creators.get(work.id, []), concepts=concepts.get(work.id, [])
        )
        for work in works
    }


async def get_work_presentation(
    session: AsyncSession, work_id: uuid.UUID, *, user_id: uuid.UUID | None = None
) -> WorkPresentation | None:
    """One work for the product surface, with the caller's own state if any.

    `user_id` comes from the resolved session and never from the request, so
    a caller cannot ask for somebody else's state. When it is None the
    response is exactly what an anonymous request receives.
    """
    work = (
        await session.execute(
            select(Work).options(selectinload(Work.domain)).where(Work.id == work_id)
        )
    ).scalar_one_or_none()
    if work is None:
        return None

    projected = await product_works_for(session, [work])

    interaction = None
    if user_id is not None:
        interaction = (
            await session.execute(
                select(UserContentInteraction).where(
                    UserContentInteraction.user_id == user_id,
                    UserContentInteraction.work_id == work_id,
                )
            )
        ).scalar_one_or_none()

    return WorkPresentation(
        work=projected[work.id], user_state=build_user_state(interaction)
    )


async def presentations_for(
    session: AsyncSession,
    works: list[Work],
    *,
    user_id: uuid.UUID | None = None,
) -> list[WorkPresentation]:
    """Wrap an already-selected list of works for the product surface.

    Split out in Phase 1Y so `discovery_service` can decide *which* works
    come back -- with filters, ordering and paging this module has no opinion
    about -- while projection stays in one place. Order is preserved exactly
    as handed in: the caller has already decided it, and a second opinion
    here would silently undo a deliberate ranking.

    `user_id` comes from the resolved session and never from a request
    parameter. When it is None the result is exactly what an anonymous caller
    receives.
    """
    if not works:
        return []

    projected = await product_works_for(session, works)

    states: dict[uuid.UUID, UserContentInteraction] = {}
    if user_id is not None:
        rows = (
            (
                await session.execute(
                    select(UserContentInteraction).where(
                        UserContentInteraction.user_id == user_id,
                        UserContentInteraction.work_id.in_(
                            [work.id for work in works]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        states = {row.work_id: row for row in rows}

    return [
        WorkPresentation(
            work=projected[work.id], user_state=build_user_state(states.get(work.id))
        )
        for work in works
    ]


async def list_work_presentations(
    session: AsyncSession,
    *,
    domain_slug: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user_id: uuid.UUID | None = None,
) -> list[WorkPresentation]:
    """The browsable catalogue, as the product sees it.

    Selection is `discovery_service`'s since Phase 1Y, so there is exactly
    one definition of which works a listing returns and in what order. Two
    implementations of the same listing would eventually disagree about a
    page boundary, and the one that disagreed would be whichever was not
    under test.
    """
    from app.services import discovery_service

    works = await discovery_service.list_works(
        session, domain_slug=domain_slug, limit=limit, offset=offset
    )
    return await presentations_for(session, works, user_id=user_id)
