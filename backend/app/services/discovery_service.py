"""Browsing the canonical corpus: filters, keyword search, facets.

Phase 1Y. The query half of the product surface. `product_service` decides
what a work *looks like* to a reader; this module decides *which works come
back* and in what order, and it reads the same canonical tables without
writing to any of them.

Everything here is server-side on purpose. Seventeen works would fit in a
response body, and a frontend that filtered them in React would work today
and stop working at the first import -- so the filters, the search and the
counts are all SQL, and the client receives a page.

---

What can be filtered, and why only these

    domain      Literature / Anime / Manga & Manhwa. Complete for every work
                by construction: a work cannot exist without one.
    concept     Noema's normalized cross-domain vocabulary. Present for
                every domain, which is the point of having it.
    genre       The *source's* own labels, e.g. AniList's "Psychological".
                Sparse by nature -- Gutenberg has no genre field, so no
                literature work carries one.
    q           Title text.

Deliberately not: media format, year, rating, popularity, length. Each is
either near-empty outside one source or is a ranking signal wearing a
filter's clothes. The facets endpoint reports what each filter would
actually match, so a client can hide a filter with nothing behind it rather
than offering an empty dropdown and calling it coverage.

**Genres and concepts are not the same thing and are never merged.** A genre
is one source's wording about one work; a concept is Noema's vocabulary,
applied across domains with its own provenance. Collapsing them would give
literature a fake genre list, which is exactly the fabricated metadata the
project refuses.

---

Ordering

Every query is deterministic, so a page boundary cannot drop or repeat a
work: title, then id as the tiebreaker.

Keyword search adds one ranking term before that, and it is entirely
lexical -- an exact title match first, then a prefix match, then anything
containing the query. No embedding, no score, nothing learned. Semantic
search is a different endpoint answering a different question, and this one
must keep working when someone types "Monster" and means *Monster*.
"""

import uuid

from sqlalchemy import Select, case, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Concept, Domain, Work, WorkConcept

# A page a client can render without scrolling forever, and a ceiling that
# stops one request from asking for the whole corpus as the corpus grows.
DEFAULT_PAGE_SIZE = 24
MAX_PAGE_SIZE = 100

# Postgres LIKE metacharacters. A user typing "100%" is searching for a
# title, not writing a pattern, so these are escaped rather than honoured.
_LIKE_ESCAPE = "\\"


def escape_like(value: str) -> str:
    """Neutralise LIKE metacharacters in user input.

    Without this, `%` matches everything and `_` matches any character, so a
    search box quietly becomes a pattern language nobody asked for.
    """
    out = value.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
    return out.replace("%", f"{_LIKE_ESCAPE}%").replace("_", f"{_LIKE_ESCAPE}_")


def _title_match(query: str):
    """Case-insensitive match on either title a work is known by.

    `original_title` is included because "カウボーイビバップ" and
    "Shingeki no Kyojin" are how some readers will type it, and a title
    lookup that only knows the English row is a worse lookup.
    """
    pattern = f"%{escape_like(query)}%"
    return or_(
        Work.title.ilike(pattern, escape=_LIKE_ESCAPE),
        Work.original_title.ilike(pattern, escape=_LIKE_ESCAPE),
    )


def _relevance(query: str):
    """A lexical rank: exact title, then prefix, then anything containing it.

    Three integers, not a score. Nothing is learned, weighted or tuned, and
    the ordering can be explained to a reader in one sentence -- which is the
    difference between search and the recommendation engine this phase is not
    building.
    """
    escaped = escape_like(query)
    exact = or_(
        Work.title.ilike(escaped, escape=_LIKE_ESCAPE),
        Work.original_title.ilike(escaped, escape=_LIKE_ESCAPE),
    )
    prefix = or_(
        Work.title.ilike(f"{escaped}%", escape=_LIKE_ESCAPE),
        Work.original_title.ilike(f"{escaped}%", escape=_LIKE_ESCAPE),
    )
    return case((exact, 0), (prefix, 1), else_=2)


def discovery_query(
    *,
    domain_slug: str | None = None,
    concept_slug: str | None = None,
    genre: str | None = None,
    query: str | None = None,
) -> Select[tuple[Work]]:
    """The filtered set of canonical works, without paging or ordering.

    Shared by the listing and its count so the two can never disagree about
    what matched.
    """
    statement = select(Work)

    if domain_slug is not None:
        statement = statement.join(Domain, Work.domain_id == Domain.id).where(
            Domain.slug == domain_slug
        )

    if concept_slug is not None:
        # EXISTS rather than a join: a work matches once however many
        # association rows it has, and no DISTINCT is needed to say so.
        statement = statement.where(
            select(WorkConcept.id)
            .join(Concept, Concept.id == WorkConcept.concept_id)
            .where(WorkConcept.work_id == Work.id, Concept.slug == concept_slug)
            .exists()
        )

    if genre is not None:
        # Source-native genres live in the ingested payload as a JSON array.
        # Matched with a containment test on the array itself rather than a
        # LIKE over the serialized document, so "Drama" cannot match
        # "Psychological Drama" by accident.
        statement = statement.where(
            text(
                "works.extra_metadata->'anilist'->'genres'"
                " @> jsonb_build_array(cast(:genre as text))"
            ).bindparams(genre=genre)
        )

    if query:
        statement = statement.where(_title_match(query))

    return statement


def _ordered(statement: Select[tuple[Work]], query: str | None) -> Select[tuple[Work]]:
    """Deterministic ordering, so paging cannot drop or repeat a work."""
    if query:
        return statement.order_by(_relevance(query), Work.title, Work.id)
    return statement.order_by(Work.title, Work.id)


async def count_works(
    session: AsyncSession,
    *,
    domain_slug: str | None = None,
    concept_slug: str | None = None,
    genre: str | None = None,
    query: str | None = None,
) -> int:
    """How many works match, independent of the page being asked for."""
    inner = discovery_query(
        domain_slug=domain_slug,
        concept_slug=concept_slug,
        genre=genre,
        query=query,
    ).with_only_columns(Work.id)
    return (
        await session.execute(
            select(func.count()).select_from(inner.subquery())
        )
    ).scalar_one()


async def list_works(
    session: AsyncSession,
    *,
    domain_slug: str | None = None,
    concept_slug: str | None = None,
    genre: str | None = None,
    query: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> list[Work]:
    """One page of matching works, domains eagerly loaded."""
    statement = _ordered(
        discovery_query(
            domain_slug=domain_slug,
            concept_slug=concept_slug,
            genre=genre,
            query=query,
        ),
        query,
    ).options(selectinload(Work.domain))
    rows = await session.execute(statement.limit(limit).offset(offset))
    return list(rows.scalars().all())


async def domain_facets(session: AsyncSession) -> list[tuple[str, str, int]]:
    """Every domain and how many works it holds, including empty ones.

    Empty domains are kept because a domain is part of what Noema says it
    covers; a filter that vanishes when a corpus is thin would misrepresent
    the product rather than the data.
    """
    rows = await session.execute(
        select(Domain.slug, Domain.name, func.count(Work.id))
        .outerjoin(Work, Work.domain_id == Domain.id)
        .group_by(Domain.slug, Domain.name)
        .order_by(Domain.name)
    )
    return [(slug, name, count) for slug, name, count in rows.all()]


async def concept_facets(session: AsyncSession) -> list[tuple[str, str, int]]:
    """Concepts that actually match something, commonest first.

    A concept with no works behind it is not offered. Ties break on name, so
    the list is stable between requests.
    """
    rows = await session.execute(
        select(Concept.slug, Concept.name, func.count(func.distinct(WorkConcept.work_id)))
        .join(WorkConcept, WorkConcept.concept_id == Concept.id)
        .group_by(Concept.slug, Concept.name)
        .having(func.count(func.distinct(WorkConcept.work_id)) > 0)
        .order_by(func.count(func.distinct(WorkConcept.work_id)).desc(), Concept.name)
    )
    return [(slug, name, count) for slug, name, count in rows.all()]


async def genre_facets(session: AsyncSession) -> list[tuple[str, str, int]]:
    """Source-native genre labels, unwrapped from the ingested payload.

    Sparse on purpose. Gutenberg states no genres, so literature contributes
    nothing here and the counts say so rather than the UI pretending
    otherwise.
    """
    rows = await session.execute(
        text(
            "SELECT genre, count(*) AS n FROM ("
            "  SELECT jsonb_array_elements_text("
            "    works.extra_metadata->'anilist'->'genres'"
            "  ) AS genre"
            "  FROM works"
            "  WHERE jsonb_typeof("
            "    coalesce(works.extra_metadata->'anilist'->'genres', 'null'::jsonb)"
            "  ) = 'array'"
            ") g GROUP BY genre ORDER BY n DESC, genre"
        )
    )
    # The label is the source's own wording; there is no display mapping,
    # because inventing one would be editing what the source said.
    return [(genre, genre, count) for genre, count in rows.all()]


async def works_by_ids(
    session: AsyncSession, work_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Work]:
    """Look several works up at once, for callers holding ids already."""
    if not work_ids:
        return {}
    rows = await session.execute(
        select(Work).options(selectinload(Work.domain)).where(Work.id.in_(work_ids))
    )
    return {work.id: work for work in rows.scalars().all()}


def page_bounds(page: int, page_size: int) -> tuple[int, int]:
    """Translate a 1-based page into limit/offset."""
    size = max(1, min(page_size, MAX_PAGE_SIZE))
    return size, (max(1, page) - 1) * size


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "concept_facets",
    "count_works",
    "discovery_query",
    "domain_facets",
    "escape_like",
    "genre_facets",
    "list_works",
    "page_bounds",
    "works_by_ids",
]
