"""Projecting the taste dashboard onto its public contract.

Phase 1W. A pure mapping, in the same spirit as `product.py` projects Phase
1O's evidence onto the preference page: it computes nothing, and every value
it emits is either copied from the dashboard or is a word describing a number
the dashboard already produced.

What it decides is what a reader may see, which is a real editorial choice in
four places:

  Dropping internals   `preference_evidence`, `confidence`, supporting work
                       ids, the rating mean, the feature family and the
                       pattern status do not cross this boundary. The group
                       and the band say what they meant without inviting a
                       0.81 to be read as a percentage.

  Naming domains       A reader sees "Anime" and "Literature", not `anime`
                       and `manhwa`. The mapping is Phase 1P's, reused.

  Mixed evidence       A single flag derived from two counts Phase 1O already
                       computed, so a client can hedge on "the ratings behind
                       this disagree" without being handed the agreement term
                       or being left to guess from a confidence number.

  Profile state        One word for where a profile is in its life, so a
                       client chooses between an onboarding prompt and a
                       profile without reimplementing the distinction from
                       counts. Derived here rather than stored on the
                       dashboard, which stays a structure of plain integers.

Nothing is reordered. The dashboard already sorted every group with Phase
1R's key, and re-sorting here would be a second opinion about the same
question.
"""

from app.schemas.taste_dashboard import (
    DashboardSummaryRead,
    EvidenceSummaryRead,
    FeatureRead,
    PreferenceItemRead,
    StandoutObservationRead,
    TasteDashboardResponse,
)
from app.services.preference.dashboard import (
    PreferenceItem,
    StandoutObservation,
    TasteDashboard,
)

# Domain slugs are internal; a reader sees the domain's display name. The
# same table `product.py` uses -- one place would be better, but importing a
# private name across product surfaces is worse, and the set is fixed and
# tiny. A test asserts the two stay identical.
DOMAIN_NAMES = {
    "literature": "Literature",
    "anime": "Anime",
    "manhwa": "Manga & Manhwa",
}

PROFILE_STATE_NO_ACTIVITY = "no_activity"
PROFILE_STATE_NO_RATINGS = "no_ratings"
PROFILE_STATE_BUILDING = "building"
PROFILE_STATE_ESTABLISHED = "established"


def _domain_names(slugs: tuple[str, ...]) -> list[str]:
    return [DOMAIN_NAMES.get(slug, slug) for slug in slugs]


def _features(item) -> list[FeatureRead]:
    return [FeatureRead(key=f.key, name=f.name) for f in item.features]


def _evidence(item: PreferenceItem) -> EvidenceSummaryRead:
    evidence = item.evidence
    return EvidenceSummaryRead(
        rated_works=evidence.works_rated,
        supporting_works=evidence.works_exposed,
        domains=_domain_names(item.domains),
        includes_reconsumed_works=evidence.works_reconsumed > 0,
        # Both sides of the reader's own baseline are represented, so the
        # ratings behind this preference do not agree with each other.
        has_mixed_evidence=(
            evidence.ratings_above_baseline > 0
            and evidence.ratings_below_baseline > 0
        ),
    )


def _item(item: PreferenceItem) -> PreferenceItemRead:
    return PreferenceItemRead(
        key=item.key,
        display_name=item.display_name,
        features=_features(item),
        kind=item.kind,
        direction=item.direction,
        confidence_band=item.confidence_band,
        presentation_key=item.presentation_key,
        domains=_domain_names(item.domains),
        evidence_summary=_evidence(item),
        # Display names rather than slugs: these are shown, not switched on.
        also_supported_by=list(item.also_supported_by),
    )


def _standout(observation: StandoutObservation) -> StandoutObservationRead:
    return StandoutObservationRead(
        observation=observation.observation,
        presentation_key=observation.presentation_key,
        features=_features(observation),
        domains=_domain_names(observation.domains),
        confidence_band=observation.confidence_band,
        rated_works=observation.works_rated,
    )


def profile_state(dashboard: TasteDashboard) -> str:
    """Where this profile is in its life, in one word.

    The distinction a client actually needs: has the reader done anything at
    all, have they rated anything, has any of it settled into a preference.
    """
    summary = dashboard.summary
    if summary is None:
        return PROFILE_STATE_NO_ACTIVITY
    if summary.works_rated == 0:
        # No ratings at all. Whether they have tracked anything is the
        # difference between "get started" and "rate what you have".
        return (
            PROFILE_STATE_NO_RATINGS
            if summary.total_interactions > 0
            else PROFILE_STATE_NO_ACTIVITY
        )
    if summary.concepts_with_established_evidence == 0:
        return PROFILE_STATE_BUILDING
    return PROFILE_STATE_ESTABLISHED


def to_response(dashboard: TasteDashboard) -> TasteDashboardResponse:
    """The public payload for one reader's dashboard."""
    summary = dashboard.summary
    return TasteDashboardResponse(
        summary=DashboardSummaryRead(
            profile_state=profile_state(dashboard),
            rated_works=summary.works_rated if summary else 0,
            established_preferences=(
                summary.concepts_with_established_evidence if summary else 0
            ),
            emerging_signals=summary.emerging_signals if summary else 0,
        ),
        strongly_likes=[_item(i) for i in dashboard.strongly_likes],
        mildly_likes=[_item(i) for i in dashboard.mildly_likes],
        dislikes=[_item(i) for i in dashboard.dislikes],
        emerging=[_item(i) for i in dashboard.emerging],
        what_stands_out=[_standout(o) for o in dashboard.what_stands_out],
    )
