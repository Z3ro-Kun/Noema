from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models import Domain
from app.schemas.search import (
    VALID_REPRESENTATIONS,
    VALID_TEXT_TIERS,
    SearchHitRead,
    SemanticSearchRequest,
    SemanticSearchResponse,
)
from app.services.embedding.encoder import Encoder, get_encoder
from app.services.embedding.search import EmptyQueryError, semantic_search

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


@router.post("/search/semantic", response_model=SemanticSearchResponse)
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
