"""The canonical corpus, as the product browses it.

Two contracts in one module, and the split matters. `/works`, `/works/{id}`
and `/works/{id}/concepts` are the **product** surface: they carry
`ProductWork`, which is canonical, identical for every viewer, and free of
content units, passages, embeddings and ingestion provenance. `/internal`,
`/containers` and `/content-units` are the **development** corpus viewer,
and say so.

Phase 1Y turned the listing into a proper discovery endpoint: server-side
filters, a page rather than a slice, and a facets route saying what those
filters would actually match. Nothing about it is personalized -- a signed-in
caller gets their own `user_state` attached to the same works in the same
order, and never a different set of works.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_optional, get_db
from app.models import User
from app.schemas.catalog import (
    ContainerRead,
    ContentUnitRead,
    DomainRead,
    EntityRead,
    RelationshipRead,
    WorkConceptRead,
    WorkDetail,
)
from app.schemas.discovery import DiscoveryFacets, FacetValue, WorkListResponse
from app.schemas.product import WorkPresentation
from app.services import catalog_service, discovery_service, product_service

router = APIRouter()


@router.get("/domains", response_model=list[DomainRead])
async def read_domains(db: AsyncSession = Depends(get_db)) -> list[DomainRead]:
    domains = await catalog_service.list_domains(db)
    return [DomainRead.model_validate(domain) for domain in domains]


@router.get("/works", response_model=WorkListResponse)
async def read_works(
    domain: str | None = Query(default=None, description="Domain slug."),
    concept: str | None = Query(
        default=None, description="Concept slug from Noema's vocabulary."
    ),
    genre: str | None = Query(
        default=None, description="Source-native genre label, exactly as stated."
    ),
    q: str | None = Query(
        default=None,
        max_length=200,
        description="Title text. Lexical, case-insensitive; not semantic search.",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(
        default=discovery_service.DEFAULT_PAGE_SIZE,
        ge=1,
        le=discovery_service.MAX_PAGE_SIZE,
    ),
    user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
) -> WorkListResponse:
    """Browse and search the canonical corpus.

    Open to anonymous callers, and the canonical half is identical either
    way. Signing in attaches `user_state` for works the caller already holds;
    it never changes `work`, never reorders the page and never changes which
    works match. **This is not a recommendation endpoint** -- two readers
    sending the same query get the same works.

    Filters combine with AND. `q` is a lexical title lookup: someone typing
    "Monster" is looking for *Monster*, and that must not depend on an
    embedding model. Meaning-oriented retrieval is `/search/semantic`, which
    answers a different question and says so.

    `total` counts everything that matched, not what is on this page.
    """
    limit, offset = discovery_service.page_bounds(page, page_size)
    filters = {
        "domain_slug": domain,
        "concept_slug": concept,
        "genre": genre,
        # An empty or whitespace-only q is no filter at all, rather than a
        # match-everything pattern.
        "query": (q or "").strip() or None,
    }

    works = await discovery_service.list_works(db, limit=limit, offset=offset, **filters)
    total = await discovery_service.count_works(db, **filters)

    return WorkListResponse(
        items=await product_service.presentations_for(
            db, works, user_id=user.id if user else None
        ),
        total=total,
        page=page,
        page_size=limit,
    )


@router.get("/works/facets", response_model=DiscoveryFacets)
async def read_discovery_facets(db: AsyncSession = Depends(get_db)) -> DiscoveryFacets:
    """What the discovery filters would actually match.

    Returned rather than hardcoded in a client, because Noema's metadata is
    unevenly covered on purpose: every work has a domain, most have concepts,
    and only AniList-sourced works have genres. A client can hide a filter
    with nothing behind it instead of offering an empty dropdown -- and
    nothing here invents a value to make the three domains look symmetrical.

    Declared before `/works/{work_id}` so it is not read as a work id.
    """

    def values(rows: list[tuple[str, str, int]]) -> list[FacetValue]:
        return [
            FacetValue(value=value, label=label, count=count)
            for value, label, count in rows
        ]

    return DiscoveryFacets(
        domains=values(await discovery_service.domain_facets(db)),
        concepts=values(await discovery_service.concept_facets(db)),
        genres=values(await discovery_service.genre_facets(db)),
    )


@router.get("/works/{work_id}", response_model=WorkPresentation)
async def read_work(
    work_id: uuid.UUID,
    user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
) -> WorkPresentation:
    """One work, as the product shows it.

    Carries no ingestion internals: no adapter names, no raw source tags, no
    containers, no content units, no embeddings. `/works/{id}/internal` is
    where that lives, for the development corpus viewer.
    """
    presentation = await product_service.get_work_presentation(
        db, work_id, user_id=user.id if user else None
    )
    if presentation is None:
        raise HTTPException(status_code=404, detail="work not found")
    return presentation


@router.get("/works/{work_id}/internal", response_model=WorkDetail)
async def read_work_internal(
    work_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> WorkDetail:
    """The raw catalogue record, including ingestion provenance.

    A development/debugging surface, not part of the product contract. It is
    the shape `/works/{id}` used to return before the product layer existed.
    """
    work = await catalog_service.get_work(db, work_id)
    if work is None:
        raise HTTPException(status_code=404, detail="work not found")
    return WorkDetail(
        id=work.id,
        title=work.title,
        original_title=work.original_title,
        domain_slug=work.domain.slug,
        source=work.source,
        created_at=work.created_at,
        description=work.description,
        external_ids=work.external_ids,
        extra_metadata=work.extra_metadata,
    )


@router.get("/works/{work_id}/containers", response_model=list[ContainerRead])
async def read_work_containers(
    work_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[ContainerRead]:
    if await catalog_service.get_work(db, work_id) is None:
        raise HTTPException(status_code=404, detail="work not found")

    containers = await catalog_service.list_containers(db, work_id)
    return [
        ContainerRead(
            id=container.id,
            container_type=container.container_type,
            sequence_number=container.sequence_number,
            title=container.title,
            extra_metadata=container.extra_metadata,
            content_unit_count=await catalog_service.count_content_units(db, container.id),
        )
        for container in containers
    ]


@router.get("/works/{work_id}/entities", response_model=list[EntityRead])
async def read_work_entities(
    work_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[EntityRead]:
    """Entities scoped to this work (anime characters today)."""
    if await catalog_service.get_work(db, work_id) is None:
        raise HTTPException(status_code=404, detail="work not found")

    entities = await catalog_service.list_entities(db, work_id)
    return [EntityRead.model_validate(entity) for entity in entities]


@router.get("/works/{work_id}/relationships", response_model=list[RelationshipRead])
async def read_work_relationships(
    work_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[RelationshipRead]:
    """Relationships where this work is the subject.

    Every row carries `source`/`method` so a source-provided fact is never
    displayed as though Noema inferred it.
    """
    if await catalog_service.get_work(db, work_id) is None:
        raise HTTPException(status_code=404, detail="work not found")

    rows = await catalog_service.list_work_relationships(db, work_id)
    return [
        RelationshipRead(
            id=rel.id,
            predicate=rel.predicate,
            object_type=rel.object_type,
            object_id=rel.object_id,
            object_title=title,
            source=rel.source,
            method=rel.method,
            score=rel.score,
            confidence=rel.confidence,
        )
        for rel, title in rows
    ]


@router.get("/containers/{container_id}/content-units", response_model=list[ContentUnitRead])
async def read_container_content_units(
    container_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[ContentUnitRead]:
    if await catalog_service.get_container(db, container_id) is None:
        raise HTTPException(status_code=404, detail="container not found")

    units = await catalog_service.list_content_units(db, container_id, limit=limit, offset=offset)
    return [ContentUnitRead.model_validate(unit) for unit in units]


@router.get("/works/{work_id}/concepts", response_model=list[WorkConceptRead])
async def read_work_concepts(
    work_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[WorkConceptRead]:
    """Work-level concepts, which are canonical and identical for every user.

    Every row carries `source`/`method`, so a characterization is never
    presented as an objective fact about the work. Nothing here is
    user-specific; a user's relationship to a work lives in their library.
    """
    if await catalog_service.get_work(db, work_id) is None:
        raise HTTPException(status_code=404, detail="work not found")

    return [
        WorkConceptRead(
            slug=concept.slug,
            name=concept.name,
            concept_type=concept.concept_type,
            description=concept.description,
            source=association.source,
            method=association.method,
            confidence=association.confidence,
        )
        for association, concept in await catalog_service.list_work_concepts(db, work_id)
    ]
