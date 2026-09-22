"""Content-based discovery for the authenticated reader, and what they say back.

Three routes: the shelf, and the two directions of "not interested". The user
comes from the resolved session and there is no `user_id` parameter in any
path, query or body -- the same isolation rule the library and preference APIs
follow, and the reason a caller cannot ask for somebody else's shelf, or
suppress a work on somebody else's behalf, by editing a request.

Anonymous callers get 401 rather than a generic shelf. A recommendation here
means "your evidence points at this", and there is no evidence without a
reader; the catalogue and semantic search already serve everyone, and that is
what a signed-out client should show.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_db
from app.models import User, Work
from app.schemas.recommendation import (
    RecommendationFeedbackCreate,
    RecommendationFeedbackRead,
    RecommendationRead,
    RecommendationReasonRead,
    RecommendationResponse,
    RecommendationSummaryRead,
)
from app.services import product_service, recommendation as recommendation_service

router = APIRouter(prefix="/recommendations")


def _reasons(reasons) -> list[RecommendationReasonRead]:
    return [RecommendationReasonRead.model_validate(reason) for reason in reasons]


@router.get("", response_model=RecommendationResponse)
async def read_recommendations(
    limit: int = Query(
        default=recommendation_service.DEFAULT_LIMIT, ge=1, le=50
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RecommendationResponse:
    """Catalogue works this reader has not met, from their own established taste.

    The chain is the reader's: established preferences from their taste
    profile, the concepts those preferences are about, and the catalogue works
    carrying them. No other user's history is read, no popularity is consulted
    and no semantic similarity is involved -- so every recommendation can be
    traced to a preference the reader can see on their own profile page.

    The ordering score stays inside the service. What comes back is the work,
    the preferences that matched it and a confidence band, which is the part a
    reader can check.
    """
    result = await recommendation_service.build_recommendations(
        db, user.id, limit=limit
    )

    summary = RecommendationSummaryRead(
        state=result.state,
        established_preferences=result.established_preferences,
        candidates_considered=result.candidates_considered,
        candidates_matched=result.candidates_matched,
    )
    if not result.recommendations:
        return RecommendationResponse(summary=summary, recommendations=[])

    # The ordinary product projection, in the order the service decided.
    # `domain` eagerly, because the projection reads it and a lazy load on an
    # async session is IO from a context that cannot await it.
    by_id = {
        work.id: work
        for work in (
            await db.execute(
                select(Work)
                .options(selectinload(Work.domain))
                .where(
                    Work.id.in_([item.work_id for item in result.recommendations])
                )
            )
        )
        .scalars()
        .all()
    }
    ordered = [
        by_id[item.work_id] for item in result.recommendations if item.work_id in by_id
    ]
    presentations = {
        presentation.work.id: presentation
        for presentation in await product_service.presentations_for(
            db, ordered, user_id=user.id
        )
    }

    recommendations: list[RecommendationRead] = []
    for item in result.recommendations:
        presentation = presentations.get(item.work_id)
        if presentation is None:
            # The work vanished between scoring and projection. Dropping it is
            # better than emitting a recommendation with no work.
            continue
        recommendations.append(
            RecommendationRead(
                work=presentation.work,
                user_state=presentation.user_state,
                reasons=_reasons(item.reasons),
                cautions=_reasons(item.cautions),
                confidence_band=item.confidence_band,
            )
        )

    return RecommendationResponse(summary=summary, recommendations=recommendations)


_UNKNOWN_WORK = HTTPException(status_code=404, detail="no such work")


@router.post(
    "/{work_id}/feedback",
    response_model=RecommendationFeedbackRead,
    status_code=status.HTTP_201_CREATED,
)
async def submit_recommendation_feedback(
    work_id: uuid.UUID,
    request: RecommendationFeedbackCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RecommendationFeedbackRead:
    """"Do not recommend this work to me."

    A standing instruction about one work's recommendability, and the fifth
    distinct thing a reader can say here. It is not a rating, not a dislike of
    the work or its concepts, not a library removal and not abandonment -- it
    is stored in its own table and reaches nothing the preference engine
    reads. The one effect is that the work stops being a recommendation
    candidate for this reader.

    Idempotent: saying it again returns the row the first call wrote, with the
    timestamp of when they actually decided.
    """
    try:
        feedback = await recommendation_service.suppress_recommendation(
            db, user_id=user.id, work_id=work_id, action=request.action
        )
    except recommendation_service.WorkNotFoundError:
        raise _UNKNOWN_WORK from None

    await db.commit()
    return RecommendationFeedbackRead.model_validate(feedback)


@router.delete("/{work_id}/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def withdraw_recommendation_feedback(
    work_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Take the instruction back, so the work can be recommended again.

    404 when there was nothing to take back, which is the same shape the
    library uses for the same situation. Repeating a successful withdrawal is
    therefore a 404 rather than a silent success: the reader is told the state
    they asked for is already the state.
    """
    removed = await recommendation_service.restore_recommendation(
        db, user_id=user.id, work_id=work_id
    )
    if not removed:
        raise HTTPException(status_code=404, detail="no feedback recorded for that work")

    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
