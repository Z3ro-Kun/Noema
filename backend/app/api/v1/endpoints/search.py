from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user_optional, get_db, require_internal_surface
from app.models import Domain, User, Work
from app.schemas.search import (
    VALID_REPRESENTATIONS,
    VALID_TEXT_TIERS,
    SearchHitRead,
    SemanticSearchRequest,
    SemanticSearchResponse,
    WorkSearchEvidence,
    WorkSearchHitRead,
    WorkSearchResponse,
)
from app.services import product_service
from app.services.embedding.encoder import Encoder, get_encoder
from app.services.embedding.search import EmptyQueryError, semantic_search
from app.services.embedding.work_search import candidate_pool_size, semantic_work_search

router = APIRouter()

_encoder: Encoder | None = None


def get_search_encoder() -> Encoder:
    """The query encoder, loaded once per process and reused.

    Overridable as a FastAPI dependency so tests never load a real model.
    """
    global _encoder
    if _encoder is None:
        _encoder = get_encoder()
    return _encoder


async def _validate(request: SemanticSearchRequest, db: AsyncSession) -> None:
    """Refusals shared by both search surfaces.

    One copy, so the raw endpoint and the work endpoint cannot come to
    disagree about what a valid request is.
    """
    if not request.query.strip():
        raise HTTPException(status_code=422, detail="query must not be empty")

    if request.text_tier is not None and request.text_tier not in VALID_TEXT_TIERS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown text_tier; expected one of {sorted(VALID_TEXT_TIERS)}",
        )

    if request.representation not in VALID_REPRESENTATIONS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown representation; expected one of {sorted(VALID_REPRESENTATIONS)}",
        )

    if request.domain is not None:
        known = (
            await db.execute(select(Domain.slug).where(Domain.slug == request.domain))
        ).scalar_one_or_none()
        if known is None:
            raise HTTPException(status_code=422, detail=f"unknown domain {request.domain!r}")


@router.post(
    "/search/semantic",
    response_model=SemanticSearchResponse,
    dependencies=[Depends(require_internal_surface)],
)
async def search_semantic(
    request: SemanticSearchRequest,
    db: AsyncSession = Depends(get_db),
    encoder: Encoder = Depends(get_search_encoder),
) -> SemanticSearchResponse:
    """Nearest-neighbour search over ContentUnit embeddings.

    Returns vector similarity, which is a computational observation about
    text -- not a factual, causal or thematic relationship. Nothing here is
    written to the relationships table.

    Note the corpus is not uniform: literature units are `primary` text (the
    work's own words) while anime units are `summary` text (a third party
    describing it). An unfiltered search therefore compares across tiers;
    pass `text_tier` to compare like with like.
    """
    await _validate(request, db)

    try:
        hits = await semantic_search(
            db,
            encoder,
            query=request.query,
            top_k=request.top_k,
            domain_slug=request.domain,
            text_tier=request.text_tier,
            work_id=request.work_id,
            container_id=request.container_id,
            representation=request.representation,
            grouping_config=request.grouping_config,
        )
    except EmptyQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return SemanticSearchResponse(
        query=request.query,
        model_name=encoder.model_name,
        top_k=request.top_k,
        domain=request.domain,
        text_tier=request.text_tier,
        representation=request.representation,
        hits=[SearchHitRead.model_validate(hit) for hit in hits],
    )


@router.post("/search/works", response_model=WorkSearchResponse)
async def search_works(
    request: SemanticSearchRequest,
    user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    encoder: Encoder = Depends(get_search_encoder),
) -> WorkSearchResponse:
    """Semantic search, answered in works.

    The product surface. `/search/semantic` above returns the raw passages
    the vectors actually matched and remains the retrieval-inspection
    surface; this folds those passages into the works they came from, so a
    novel that matches in four places is one result rather than four.

    `top_k` means unique works. A wider candidate pool is retrieved to make
    that possible -- filters are applied in SQL, before the fold, so a domain
    filter narrows what is searched rather than what survives it.

    The score is the strongest underlying passage similarity, unchanged: no
    IDF, no rarity or novelty weighting, no personalisation, no composite
    ranking. Open to anonymous callers, and the canonical half is identical
    either way; signing in attaches `user_state` for works the caller holds
    and changes neither the matching nor the order.
    """
    await _validate(request, db)

    try:
        matches = await semantic_work_search(
            db,
            encoder,
            query=request.query,
            top_k=request.top_k,
            domain_slug=request.domain,
            text_tier=request.text_tier,
            work_id=request.work_id,
            container_id=request.container_id,
            representation=request.representation,
            grouping_config=request.grouping_config,
        )
    except EmptyQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # One query for every matched work, then the ordinary product projection
    # -- the same one Discover and the library use, so a search result is the
    # same object a work card already knows how to render.
    works = list(
        (
            await db.execute(
                # `domain` eagerly, because the projection below reads it and
                # a lazy load on an async session is IO from a context that
                # cannot await it -- a 500, not a slow response.
                select(Work)
                .options(selectinload(Work.domain))
                .where(Work.id.in_([match.work_id for match in matches]))
            )
        )
        .scalars()
        .all()
    )
    presentations = {
        presentation.work.id: presentation
        for presentation in await product_service.presentations_for(
            db, works, user_id=user.id if user else None
        )
    }

    results: list[WorkSearchHitRead] = []
    for match in matches:
        presentation = presentations.get(match.work_id)
        if presentation is None:
            # The work vanished between the vector query and the projection.
            # Dropping it is better than emitting a result with no work.
            continue
        results.append(
            WorkSearchHitRead(
                work=presentation.work,
                user_state=presentation.user_state,
                similarity=match.similarity,
                distance=match.distance,
                representation=match.representation,
                evidence=WorkSearchEvidence.model_validate(match),
            )
        )

    return WorkSearchResponse(
        query=request.query,
        model_name=encoder.model_name,
        top_k=request.top_k,
        domain=request.domain,
        text_tier=request.text_tier,
        representation=request.representation,
        candidates_examined=candidate_pool_size(request.top_k),
        results=results,
    )
