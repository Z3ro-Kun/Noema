"""Preference evidence for the authenticated user.

Three kinds of surface live here. `/dashboard` is the product contract added
in Phase 1W -- the taste profile a client renders. `/feedback` is Phase 1X's
other direction: what the reader says back about that profile. The rest are
development and evaluation surfaces: they exist so the evidence layer can be
inspected against real histories, and they expose internals the dashboard
deliberately does not. There is no personality profile behind any of them.

Observed preference and explicit feedback stay separate all the way down --
separate tables, separate routes, separate meanings. A reader disagreeing
with a reading is not a rating, and in this phase it changes nothing about
what `/dashboard` returns.

The user always comes from the resolved session. There is no `user_id`
parameter anywhere in this module, so a caller cannot ask for somebody
else's evidence by editing a request -- the same isolation rule the library
API follows.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models import User
from app.schemas.preference import ConceptEvidenceRead, PreferenceProfileRead
from app.schemas.preference_feedback import (
    FeedbackVocabulary,
    PreferenceFeedbackCreate,
    PreferenceFeedbackEventRead,
    PreferenceFeedbackHistory,
    PreferenceFeedbackList,
    PreferenceFeedbackRead,
)
from app.schemas.preference_product import PreferenceOverview
from app.schemas.taste_dashboard import TasteDashboardResponse
from app.services.preference import feedback as feedback_service
from app.services.preference.dashboard import build_taste_dashboard
from app.services.preference.dashboard_product import to_response
from app.services.preference.evidence import (
    build_preference_profile,
    concept_evidence_for,
)
from app.services.preference.product import build_preference_overview

router = APIRouter(prefix="/preferences")


@router.get("/overview", response_model=PreferenceOverview)
async def read_preference_overview(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PreferenceOverview:
    """The preference page's payload: media preference evidence, no traits.

    The product contract. Carries no raw scores, no normalization internals
    and no content-annotation confidence -- direction and a confidence band
    say the same thing without inviting a 0.81 to be read as a percentage.

    Concepts the user has met but never rated are returned in their own list
    rather than as a direction, so engagement is never shown as approval.
    """
    return await build_preference_overview(db, user.id)


@router.get("/dashboard", response_model=TasteDashboardResponse)
async def read_taste_dashboard(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TasteDashboardResponse:
    """This reader's taste profile: three groups, early signals, observations.

    Derived on every request from the current interaction history, through
    the whole pipeline -- preference signals, taste patterns, profile
    composition, insights -- and stored nowhere. Rate something new and the
    next call reflects it; nothing is a standing verdict.

    The payload carries meaning rather than machinery. A group says how much
    a reader liked something and a confidence band says how much evidence
    there is for saying so; the two are independent, so a strong preference
    with moderate confidence is a normal and well-formed answer. No
    preference evidence, normalization internals, annotation provenance or
    work ids cross this boundary.

    Nothing here is a claim about the person: no trait, no diagnosis, no
    account of *why* anything was rated -- which Noema does not know.
    """
    dashboard = await build_taste_dashboard(db, user.id)
    return to_response(dashboard)


def _feedback_read(row) -> PreferenceFeedbackRead:
    """One stored verdict, named by the slug the client already holds."""
    return PreferenceFeedbackRead(
        concept_slug=row.concept.slug,
        concept_name=row.concept.name,
        feedback=row.feedback_type,
        source=row.source_surface,
        submission_count=row.submission_count,
        first_recorded_at=row.first_recorded_at,
        updated_at=row.updated_at,
    )


@router.get("/feedback/vocabulary", response_model=FeedbackVocabulary)
async def read_feedback_vocabulary() -> FeedbackVocabulary:
    """The controlled feedback values and what each one is allowed to mean.

    Including, explicitly, that `corrected` is a disagreement with an
    interpretation and not a statement that the reader dislikes the concept,
    and that feedback does not currently move the preference engine. Both are
    easy for a client to imply by accident, so the contract says them out
    loud rather than leaving them to a comment.
    """
    return FeedbackVocabulary()


@router.get("/feedback", response_model=PreferenceFeedbackList)
async def read_preference_feedback(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PreferenceFeedbackList:
    """Everything this reader has said about Noema's readings of their taste.

    Ordered by canonical slug, so two identical histories give two identical
    payloads. Fetched beside the dashboard rather than embedded in it: the
    dashboard is Noema's interpretation and this is the reader's answer to
    it, and merging them into one object would be the first step toward
    confusing the two.
    """
    rows = await feedback_service.list_feedback(db, user_id=user.id)
    return PreferenceFeedbackList(items=[_feedback_read(row) for row in rows])


@router.post(
    "/feedback",
    response_model=PreferenceFeedbackRead,
    status_code=status.HTTP_201_CREATED,
)
async def submit_preference_feedback(
    body: PreferenceFeedbackCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PreferenceFeedbackRead:
    """Record whether one of Noema's readings feels right to this reader.

    The target is a canonical concept slug -- the same `features[].key` the
    dashboard handed out. An unknown slug is refused rather than stored as
    free text, so the table stays joinable and an opinion always has
    something real to attach to.

    **This writes no rating and changes no preference.** The verdict goes to
    its own table; `user_content_interactions` is untouched and the evidence
    the dashboard is built from is identical before and after. A `corrected`
    answer records disagreement with an interpretation and is not read as
    evidence that the reader dislikes the concept -- they may simply not care
    about it.

    Answering again updates the current verdict and appends to the history,
    so changing one's mind adds a fact instead of erasing one. 201 either
    way: every call creates an answer, even when the verdict it produces is
    the one already on record.
    """
    try:
        row = await feedback_service.record_feedback(
            db,
            user_id=user.id,
            concept_slug=body.concept_slug,
            feedback_type=body.feedback,
            source_surface=body.source,
        )
    except feedback_service.UnknownTargetError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except (
        feedback_service.InvalidFeedbackError,
        feedback_service.InvalidSurfaceError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    await db.commit()
    return _feedback_read(row)


@router.get("/feedback/{concept_slug}", response_model=PreferenceFeedbackHistory)
async def read_preference_feedback_history(
    concept_slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PreferenceFeedbackHistory:
    """One concept's current verdict and every answer behind it, oldest first.

    A concept this reader has never answered about returns a null `current`
    and no events -- not a 404, because the concept exists and "nothing said
    yet" is a real answer to the question.
    """
    try:
        current = await feedback_service.feedback_for_slug(
            db, user_id=user.id, concept_slug=concept_slug
        )
        events = await feedback_service.feedback_history(
            db, user_id=user.id, concept_slug=concept_slug
        )
    except feedback_service.UnknownTargetError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    return PreferenceFeedbackHistory(
        concept_slug=concept_slug,
        concept_name=current.concept.name if current is not None else concept_slug,
        current=None if current is None else _feedback_read(current),
        events=[
            PreferenceFeedbackEventRead(
                feedback_before=event.feedback_before,
                feedback_after=event.feedback_after,
                source=event.source_surface,
                occurred_at=event.occurred_at,
            )
            for event in events
        ],
    )


@router.get("", response_model=PreferenceProfileRead)
async def read_preference_evidence(
    min_rated: int = Query(
        default=0,
        ge=0,
        description="Only concepts supported by at least this many rated works.",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PreferenceProfileRead:
    """This user's per-concept preference evidence, strongest first.

    Computed from interactions and work-concept associations on each request;
    nothing is stored. Carries no personality claim -- every concept reports
    a direction, a confidence, and the works and ratings behind them.
    """
    profile = await build_preference_profile(db, user.id)
    concepts = [
        evidence for evidence in profile.concepts if evidence.works_rated >= min_rated
    ][:limit]

    return PreferenceProfileRead(
        rating_context=profile.rating_context,
        total_interactions=profile.total_interactions,
        interactions_without_concepts=profile.interactions_without_concepts,
        concepts=concepts,
    )


@router.get("/{concept_slug}", response_model=ConceptEvidenceRead)
async def read_concept_evidence(
    concept_slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConceptEvidenceRead:
    """One concept's evidence, with the works and ratings that produced it.

    404 when this user has no history touching the concept -- which is not
    the same as evidence that they dislike it.
    """
    evidence = await concept_evidence_for(db, user.id, concept_slug)
    if evidence is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no interaction history for that concept",
        )
    return ConceptEvidenceRead.model_validate(evidence)
