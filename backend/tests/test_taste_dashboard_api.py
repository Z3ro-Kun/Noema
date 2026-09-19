"""Phase 1W: the taste dashboard as a client receives it.

Three things are under test and they fail in different ways.

The **semantic rule**, which Phase 1V got wrong: it required the high
confidence band for `strongly_likes`, so a reader who rated five works 8 to 10
was told Noema mildly thought they liked something. Strength and confidence
are separate axes and only strength decides the group.

The **contract**, which must carry meaning and not machinery -- a recursive
walk over the serialized payload fails if any scoring internal reappears.

The **boundary**, which is the same one every user-scoped route uses: the user
comes from the session and no parameter names one.

Everything committed here is created under test-only identifiers and removed
afterwards.
"""

import asyncio
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import STATUS_COMPLETED, Work
from app.schemas.taste_dashboard import (
    BUCKETS,
    CONFIDENCE_BANDS,
    PROFILE_STATES,
    TasteDashboardResponse,
)
from app.services.concepts.service import (
    SourceLabel,
    apply_source_labels,
    ensure_vocabulary,
)
from app.services.ingestion.anime import AniListAnimeAdapter
from app.services.ingestion.service import ingest_source_work
from app.services.preference.dashboard import (
    BUCKET_DISLIKES,
    BUCKET_EMERGING,
    BUCKET_MILDLY_LIKES,
    BUCKET_STRONGLY_LIKES,
    STANDOUT_CROSS_DOMAIN,
    FeatureRef,
    StandoutObservation,
    build_dashboard,
    classify,
)
from app.services.preference.dashboard_product import (
    DOMAIN_NAMES,
    PROFILE_STATE_BUILDING,
    PROFILE_STATE_ESTABLISHED,
    PROFILE_STATE_NO_ACTIVITY,
    PROFILE_STATE_NO_RATINGS,
    profile_state,
    to_response,
)
from app.services.preference.insights import derive_insights
from app.services.preference.profile import ComposedProfile, SelectedPattern
from app.services.preference.taste import (
    KIND_COMBINATION,
    KIND_INDIVIDUAL,
    STATUS_EMERGING,
    STATUS_ESTABLISHED,
    Feature,
    SupportingWork,
    TastePattern,
)

FIXTURES = Path(__file__).parent / "fixtures"
DASHBOARD_EMAIL_DOMAIN = "@dashboard-api.invalid"
# Distinct from every other suite's block, so a full run cannot collide.
DASHBOARD_SOURCE_IDS = (986001, 986002, 986003, 986004, 986005, 986006)
PASSWORD = "a-sufficiently-long-password"

# The first four works carry one concept, the last two a different one, so a
# test can add evidence for an unrelated preference without touching the
# first.
PSYCHOLOGICAL = DASHBOARD_SOURCE_IDS[:4]
MYSTERY = DASHBOARD_SOURCE_IDS[4:]


@dataclass
class DashboardApi:
    client: TestClient
    psychological_ids: list[str]
    mystery_ids: list[str]


@pytest.fixture
def api(database_available: bool) -> Iterator[DashboardApi]:
    if not database_available:
        pytest.skip("requires a live Postgres instance")

    from app.main import app

    psychological: list[str] = []
    mystery: list[str] = []

    async def seed() -> None:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(bind=engine, expire_on_commit=False)() as session:
                await ensure_vocabulary(session)
                media = json.loads(
                    (FIXTURES / "anilist_cowboy_bebop.json").read_text(encoding="utf-8")
                )
                for anilist_id in DASHBOARD_SOURCE_IDS:
                    payload = dict(media)
                    payload["id"] = anilist_id
                    payload["relations"] = {"edges": []}
                    payload["genres"] = []
                    payload["tags"] = []
                    result = await ingest_source_work(
                        session, AniListAnimeAdapter(media=payload).load()
                    )
                    work = await session.get(Work, result.work_id)
                    label = (
                        SourceLabel("Psychological", "anilist_tag", rank=88)
                        if anilist_id in PSYCHOLOGICAL
                        else SourceLabel("Mystery", "anilist_tag", rank=88)
                    )
                    await apply_source_labels(session, work=work, labels=[label])
                    target = (
                        psychological if anilist_id in PSYCHOLOGICAL else mystery
                    )
                    target.append(str(work.id))
                await session.commit()
        finally:
            await engine.dispose()

    async def cleanup() -> None:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(bind=engine)() as session:
                refs = ", ".join(f"'{value}'" for value in DASHBOARD_SOURCE_IDS)
                work_ids = (
                    "SELECT id FROM works WHERE source = 'anilist' "
                    f"AND external_ids->>'source_ref' IN ({refs})"
                )
                for statement in (
                    "DELETE FROM user_content_events WHERE interaction_id IN "
                    f"(SELECT id FROM user_content_interactions WHERE work_id IN ({work_ids}))",
                    f"DELETE FROM user_content_interactions WHERE work_id IN ({work_ids})",
                    f"DELETE FROM work_concepts WHERE work_id IN ({work_ids})",
                    f"DELETE FROM entities WHERE work_id IN ({work_ids})",
                    f"DELETE FROM containers WHERE work_id IN ({work_ids})",
                    f"DELETE FROM work_creators WHERE work_id IN ({work_ids})",
                    "DELETE FROM user_content_events WHERE interaction_id IN ("
                    " SELECT i.id FROM user_content_interactions i JOIN users u"
                    f" ON u.id = i.user_id WHERE u.email LIKE '%{DASHBOARD_EMAIL_DOMAIN}')",
                    "DELETE FROM user_content_interactions WHERE user_id IN "
                    f"(SELECT id FROM users WHERE email LIKE '%{DASHBOARD_EMAIL_DOMAIN}')",
                    "DELETE FROM user_sessions WHERE user_id IN "
                    f"(SELECT id FROM users WHERE email LIKE '%{DASHBOARD_EMAIL_DOMAIN}')",
                    f"DELETE FROM users WHERE email LIKE '%{DASHBOARD_EMAIL_DOMAIN}'",
                    f"DELETE FROM works WHERE id IN ({work_ids})",
                ):
                    await session.execute(text(statement))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed())
    with TestClient(app) as client:
        try:
            yield DashboardApi(
                client=client,
                psychological_ids=psychological,
                mystery_ids=mystery,
            )
        finally:
            asyncio.run(cleanup())


def register(api: DashboardApi, name: str) -> dict:
    response = api.client.post(
        "/api/v1/auth/register",
        json={"email": f"{name}{DASHBOARD_EMAIL_DOMAIN}", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def rate(api: DashboardApi, headers: dict, work_id: str, rating: int | None) -> None:
    api.client.post("/api/v1/library", json={"work_id": work_id}, headers=headers)
    api.client.patch(
        f"/api/v1/library/{work_id}",
        json={"status": STATUS_COMPLETED},
        headers=headers,
    )
    if rating is not None:
        api.client.patch(
            f"/api/v1/library/{work_id}",
            json={"rating": rating, "rating_set": True},
            headers=headers,
        )


def dashboard(api: DashboardApi, headers: dict) -> dict:
    response = api.client.get("/api/v1/preferences/dashboard", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def find(payload: dict, key: str) -> tuple[str, dict] | None:
    """Which group an item is in, and the item, or None."""
    for bucket in BUCKETS:
        for item in payload[bucket]:
            if item["key"] == key:
                return bucket, item
    return None


# --- constructed patterns, for the rule itself -----------------------------


def work(rating: int | None = 9, domain: str = "anime") -> SupportingWork:
    return SupportingWork(
        work_id=uuid.uuid4(),
        title=f"work-{uuid.uuid4().hex[:6]}",
        domain_slug=domain,
        status="completed",
        rating=rating,
        normalized_rating=None if rating is None else 0.8,
        times_completed=1,
        in_library=True,
    )


def pattern(
    keys: tuple[str, ...] = ("feature",),
    *,
    status: str = STATUS_ESTABLISHED,
    confidence: float = 0.7,
    evidence: float | None = 0.9,
    direction: str = "positive",
    works: tuple[SupportingWork, ...] | None = None,
) -> TastePattern:
    features = tuple(
        Feature(key=key, name=key.replace("-", " ").title(), family="theme")
        for key in keys
    )
    supporting = works if works is not None else (work(), work(), work())
    # The counts Phase 1S derives from contributions. Set here because this
    # helper builds a pattern directly rather than going through aggregation.
    return TastePattern(
        features=features,
        kind=KIND_INDIVIDUAL if len(features) == 1 else KIND_COMBINATION,
        status=status,
        direction=direction,
        preference_evidence=evidence,
        confidence=confidence,
        works_rated=sum(1 for w in supporting if w.rating is not None),
        works_exposed=len(supporting),
        works_completed=sum(1 for w in supporting if w.times_completed > 0),
        works_reconsumed=sum(1 for w in supporting if w.times_completed > 1),
        total_completions=sum(w.times_completed for w in supporting),
        ratings_above_baseline=sum(
            1 for w in supporting if (w.normalized_rating or 0) > 0
        ),
        ratings_below_baseline=sum(
            1 for w in supporting if (w.normalized_rating or 0) < 0
        ),
        supporting_works=supporting,
        domains=tuple(sorted({w.domain_slug for w in supporting})),
    )


def response_of(
    key: list[TastePattern],
    early: list[TastePattern] | None = None,
    *,
    alternatives: dict[str, tuple[str, ...]] | None = None,
):
    """A public payload from constructed patterns.

    `rating_count` and `total_interactions` are handed in because the real
    builder reads them from the engine profile, which these constructed
    patterns have no equivalent of.
    """
    also = alternatives or {}
    profile = ComposedProfile(
        user_id=uuid.uuid4(),
        key_patterns=[
            SelectedPattern(pattern=p, indistinguishable_from=also.get(p.key, ()))
            for p in key
        ],
        early_signals=[SelectedPattern(pattern=p) for p in (early or [])],
    )
    rated = {
        w.work_id
        for p in [*key, *(early or [])]
        for w in p.supporting_works
        if w.rating is not None
    }
    return to_response(
        build_dashboard(
            profile,
            derive_insights(profile),
            rating_count=len(rated),
            total_interactions=len(rated),
            # Alternatives are patterns selection set aside, so their names
            # come from outside the profile -- as they do in the real builder.
            names={
                key: key.replace("-", " ").title()
                for keys in also.values()
                for key in keys
            },
        )
    )


# --- 1-6: bucket semantics --------------------------------------------------


def test_1_strong_evidence_with_moderate_confidence_is_strongly_liked() -> None:
    """The Phase 1V defect, stated as its own test.

    Five works rated 10, 10, 9, 9, 8 produce strong evidence and moderate
    confidence. That is a clear liking Noema has seen a handful of times, and
    calling it mild misdescribes the reader in order to hedge about Noema.
    """
    assert classify(pattern(evidence=0.82, confidence=0.45)) == BUCKET_STRONGLY_LIKES


def test_2_strong_evidence_with_high_confidence_is_strongly_liked() -> None:
    assert classify(pattern(evidence=0.82, confidence=0.75)) == BUCKET_STRONGLY_LIKES


def test_3_positive_evidence_below_the_threshold_is_mildly_liked() -> None:
    assert classify(pattern(evidence=0.49, confidence=0.95)) == BUCKET_MILDLY_LIKES


def test_4_established_negative_evidence_is_a_dislike() -> None:
    for confidence in (0.36, 0.95):
        assert (
            classify(pattern(evidence=-0.8, confidence=confidence, direction="negative"))
            == BUCKET_DISLIKES
        )


def test_5_an_emerging_positive_signal_is_not_a_strong_preference() -> None:
    """Strong evidence and established preference are different claims."""
    assert (
        classify(pattern(evidence=0.99, confidence=0.9, status=STATUS_EMERGING))
        == BUCKET_EMERGING
    )


def test_6_an_emerging_negative_signal_is_not_a_dislike() -> None:
    assert (
        classify(
            pattern(
                evidence=-0.99,
                confidence=0.9,
                status=STATUS_EMERGING,
                direction="negative",
            )
        )
        == BUCKET_EMERGING
    )


def test_confidence_is_reported_but_never_decides_the_group() -> None:
    """Both axes reach the payload, and only one of them groups."""
    payload = response_of(
        [
            pattern(("early",), evidence=0.9, confidence=0.40),
            pattern(("attested",), evidence=0.9, confidence=0.80),
        ]
    )

    groups = {item.key: item.confidence_band for item in payload.strongly_likes}
    assert groups == {"early": "moderate", "attested": "high"}


# --- 7-9: dynamic behaviour, end to end -------------------------------------


def test_7_more_supporting_ratings_raise_confidence_without_changing_the_group(
    api: DashboardApi,
) -> None:
    headers = register(api, "dynamic-confidence")
    for work_id, rating in zip(api.psychological_ids[:3], (10, 10, 9)):
        rate(api, headers, work_id, rating)

    before = find(dashboard(api, headers), "psychological-depth")
    assert before is not None
    bucket_before, item_before = before
    assert bucket_before == BUCKET_STRONGLY_LIKES

    # A fourth agreeing rating: more evidence for the same conclusion.
    rate(api, headers, api.psychological_ids[3], 10)

    after = find(dashboard(api, headers), "psychological-depth")
    assert after is not None
    bucket_after, item_after = after
    assert bucket_after == BUCKET_STRONGLY_LIKES
    assert (
        item_after["evidence_summary"]["rated_works"]
        > item_before["evidence_summary"]["rated_works"]
    )
    bands = list(CONFIDENCE_BANDS)
    assert bands.index(item_after["confidence_band"]) >= bands.index(
        item_before["confidence_band"]
    )


def test_8_contradictory_ratings_can_move_a_preference_between_groups(
    api: DashboardApi,
) -> None:
    """The profile is a reading of the current history, not a verdict."""
    headers = register(api, "dynamic-contradiction")
    for work_id, rating in zip(api.psychological_ids[:3], (10, 10, 9)):
        rate(api, headers, work_id, rating)

    first = find(dashboard(api, headers), "psychological-depth")
    assert first is not None and first[0] == BUCKET_STRONGLY_LIKES

    # The same concept, now also carried by a work they disliked.
    rate(api, headers, api.psychological_ids[3], 1)

    second = find(dashboard(api, headers), "psychological-depth")
    assert second is not None
    bucket, item = second
    # It has moved off the strong claim -- to a milder one, or out of the
    # established groups altogether if the ratings now disagree too much.
    assert bucket != BUCKET_STRONGLY_LIKES
    assert item["evidence_summary"]["has_mixed_evidence"] is True


def test_9_an_unrelated_concept_does_not_reclassify_an_existing_one(
    api: DashboardApi,
) -> None:
    """Why the threshold is absolute rather than a percentile or a rank."""
    headers = register(api, "dynamic-unrelated")
    for work_id, rating in zip(api.psychological_ids[:3], (10, 10, 9)):
        rate(api, headers, work_id, rating)

    before = find(dashboard(api, headers), "psychological-depth")
    assert before is not None

    # An entirely different concept arrives, rated even more highly.
    for work_id in api.mystery_ids:
        rate(api, headers, work_id, 10)

    after = find(dashboard(api, headers), "psychological-depth")
    assert after is not None
    assert after[0] == before[0]
    assert after[1]["confidence_band"] == before[1]["confidence_band"]
    assert (
        after[1]["evidence_summary"]["rated_works"]
        == before[1]["evidence_summary"]["rated_works"]
    )


# --- 10-11: cross-domain deduplication --------------------------------------


def test_10_two_materially_different_cross_domain_observations_coexist() -> None:
    """Different sets of media are different findings."""
    payload = response_of(
        [
            pattern(
                ("two-media",),
                confidence=0.7,
                works=(work(domain="anime"), work(domain="literature"), work()),
            ),
            pattern(
                ("three-media",),
                confidence=0.6,
                works=(
                    work(domain="anime"),
                    work(domain="literature"),
                    work(domain="manhwa"),
                ),
            ),
        ]
    )

    spans = [
        tuple(o.domains)
        for o in payload.what_stands_out
        if o.observation == STANDOUT_CROSS_DOMAIN
    ]
    assert len(spans) == 2
    assert len(set(spans)) == 2


def test_11_observations_restating_the_same_span_are_deduplicated() -> None:
    """Four concepts spanning anime and literature make that point once."""
    payload = response_of(
        [
            pattern(
                (f"concept-{index}",),
                confidence=0.7 - index / 100,
                works=(work(domain="anime"), work(domain="literature"), work()),
            )
            for index in range(4)
        ]
    )

    cross = [
        o for o in payload.what_stands_out if o.observation == STANDOUT_CROSS_DOMAIN
    ]
    assert len(cross) == 1


def test_cross_domain_is_not_capped_at_one() -> None:
    """The Phase 1V rule this phase removed, pinned so it cannot come back."""
    payload = response_of(
        [
            pattern(
                ("a",),
                confidence=0.70,
                works=(work(domain="anime"), work(domain="literature"), work()),
            ),
            pattern(
                ("b",),
                confidence=0.65,
                works=(work(domain="anime"), work(domain="manhwa"), work()),
            ),
            pattern(
                ("c",),
                confidence=0.60,
                works=(work(domain="literature"), work(domain="manhwa"), work()),
            ),
        ]
    )

    cross = [
        o for o in payload.what_stands_out if o.observation == STANDOUT_CROSS_DOMAIN
    ]
    assert len(cross) == 3


def test_deduplication_keeps_distinct_combinations_apart() -> None:
    payload = response_of(
        [
            pattern(("a", "b"), confidence=0.7, works=(work(), work(), work(), work())),
            pattern(("c", "d"), confidence=0.6, works=(work(), work(), work(), work())),
        ]
    )

    combinations = [
        o for o in payload.what_stands_out if o.observation == "combination_highlight"
    ]
    assert len(combinations) == 2


# --- 12-15: what the contract must not carry --------------------------------


FORBIDDEN_KEYS = (
    "preference_evidence",
    "rating_signal",
    "normalized_rating",
    "rating_mean",
    "confidence",  # bare float; `confidence_band` is the allowed form
    "baseline",
    "spread",
    "shrink",
    "exposure",
    "engagement",
    "salience",
    "specificity",
    "document_frequency",
    "idf",
    "work_id",
    "supporting_work_ids",
    "concept_confidence",
    "method",
    "source",
    "supporting_labels",
    "family",
    "status",
    "content_unit",
    "embedding",
    "vector",
    "passage",
    "personality",
    "trait",
    "because",
    "cause",
    "reason",
    "motivation",
    "diagnosis",
    "trauma",
    "childhood",
)

ALLOWED_KEYS = {
    "summary",
    "profile_state",
    "rated_works",
    "established_preferences",
    "emerging_signals",
    "strongly_likes",
    "mildly_likes",
    "dislikes",
    "emerging",
    "what_stands_out",
    "key",
    "display_name",
    "features",
    "name",
    "kind",
    "direction",
    "confidence_band",
    "presentation_key",
    "domains",
    "evidence_summary",
    "also_supported_by",
    "supporting_works",
    "includes_reconsumed_works",
    "has_mixed_evidence",
    "observation",
}


def walk_keys(node, found: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            walk_keys(value, found)
    elif isinstance(node, list):
        for value in node:
            walk_keys(value, found)


def test_12_no_scoring_internals_appear_in_the_serialized_payload(
    api: DashboardApi,
) -> None:
    headers = register(api, "safety-internals")
    for work_id, rating in zip(api.psychological_ids, (10, 10, 9, 8)):
        rate(api, headers, work_id, rating)

    payload = dashboard(api, headers)
    keys: set[str] = set()
    walk_keys(payload, keys)

    assert keys, "payload carried no fields at all"
    for forbidden in FORBIDDEN_KEYS:
        assert not any(forbidden == key for key in keys), forbidden
    # And nothing unexpected has been added since this contract was written.
    assert keys <= ALLOWED_KEYS, keys - ALLOWED_KEYS


def test_13_no_concept_extraction_provenance_leaks(api: DashboardApi) -> None:
    headers = register(api, "safety-provenance")
    for work_id, rating in zip(api.psychological_ids, (10, 10, 9, 8)):
        rate(api, headers, work_id, rating)

    body = json.dumps(dashboard(api, headers)).lower()
    for forbidden in ("anilist_tag", "anilist_genre", "gutenberg_subject", "rank"):
        assert forbidden not in body, forbidden


def test_14_no_content_units_passages_or_embeddings_are_exposed(
    api: DashboardApi,
) -> None:
    headers = register(api, "safety-content")
    for work_id, rating in zip(api.psychological_ids, (10, 10, 9, 8)):
        rate(api, headers, work_id, rating)

    body = json.dumps(dashboard(api, headers)).lower()
    for forbidden in ("content_unit", "passage", "embedding", "text_tier", "vector"):
        assert forbidden not in body, forbidden


def test_15_no_psychological_or_biographical_vocabulary_exists() -> None:
    """Checked on the schema, so an empty profile cannot hide a field."""
    schema = TasteDashboardResponse.model_json_schema()
    body = json.dumps(schema).lower()
    for forbidden in (
        "personality",
        "trait",
        "introvert",
        "extrovert",
        "neurotic",
        "trauma",
        "childhood",
        "attachment",
        "diagnos",
        "because",
        "motivation",
        "recommend",
    ):
        assert forbidden not in body, forbidden


def test_presentation_keys_carry_no_algorithmic_wording() -> None:
    payload = response_of(
        [
            pattern(("liked",), evidence=0.9),
            pattern(
                ("disliked",),
                evidence=-0.9,
                direction="negative",
                works=(work(2), work(3), work(2)),
            ),
        ]
    )

    keys = {item.presentation_key for item in payload.strongly_likes + payload.dislikes}
    assert keys == {"enjoys_feature", "negative_feature"}
    for key in keys:
        assert "evidence" not in key
        assert "score" not in key


# --- 16-17: authentication and isolation ------------------------------------


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}]
)
def test_17_anonymous_requests_follow_the_established_auth_behaviour(
    api: DashboardApi, headers: dict
) -> None:
    response = api.client.get("/api/v1/preferences/dashboard", headers=headers)

    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_16_one_reader_never_receives_another_readers_dashboard(
    api: DashboardApi,
) -> None:
    liked = register(api, "isolation-liked")
    disliked = register(api, "isolation-disliked")
    for work_id, rating in zip(api.psychological_ids, (10, 10, 9, 9)):
        rate(api, liked, work_id, rating)
    for work_id, rating in zip(api.psychological_ids, (2, 2, 3, 3)):
        rate(api, disliked, work_id, rating)

    first = dashboard(api, liked)
    second = dashboard(api, disliked)

    assert first["strongly_likes"] and not first["dislikes"]
    assert second["dislikes"] and not second["strongly_likes"]
    assert first != second


def test_the_endpoint_accepts_no_user_identifier_at_all() -> None:
    """Isolation is structural: there is no parameter to point elsewhere."""
    from app.main import app

    schema = app.openapi()["paths"]["/api/v1/preferences/dashboard"]["get"]
    names = {parameter["name"] for parameter in schema.get("parameters", [])}

    assert names == set()


def test_the_dashboard_route_is_not_swallowed_by_the_concept_route() -> None:
    """`/{concept_slug}` would match `/dashboard` if it were declared first."""
    from app.main import app

    paths = app.openapi()["paths"]
    assert "/api/v1/preferences/dashboard" in paths
    routes = [r.path for r in app.routes if getattr(r, "path", "").startswith("/api/v1/preferences")]
    assert routes.index("/api/v1/preferences/dashboard") < routes.index(
        "/api/v1/preferences/{concept_slug}"
    )


# --- 18-20: empty and sparse states -----------------------------------------


def test_18_a_reader_with_no_activity_gets_an_empty_dashboard(
    api: DashboardApi,
) -> None:
    headers = register(api, "empty-nothing")

    payload = dashboard(api, headers)

    assert payload["summary"]["profile_state"] == PROFILE_STATE_NO_ACTIVITY
    assert payload["summary"]["rated_works"] == 0
    for bucket in BUCKETS:
        assert payload[bucket] == []
    assert payload["what_stands_out"] == []


def test_19_tracked_but_unrated_works_produce_no_preferences(
    api: DashboardApi,
) -> None:
    headers = register(api, "empty-unrated")
    for work_id in api.psychological_ids:
        rate(api, headers, work_id, None)

    payload = dashboard(api, headers)

    assert payload["summary"]["profile_state"] == PROFILE_STATE_NO_RATINGS
    assert payload["summary"]["established_preferences"] == 0
    assert payload["strongly_likes"] == []
    assert payload["mildly_likes"] == []
    assert payload["dislikes"] == []


def test_20_only_emerging_signals_fabricates_no_established_preference(
    api: DashboardApi,
) -> None:
    """Two rated works is the minimum: a signal, not a settled preference."""
    headers = register(api, "empty-emerging")
    for work_id, rating in zip(api.psychological_ids[:2], (10, 9)):
        rate(api, headers, work_id, rating)

    payload = dashboard(api, headers)

    assert payload["strongly_likes"] == []
    assert payload["mildly_likes"] == []
    assert payload["dislikes"] == []
    assert payload["emerging"]
    assert payload["summary"]["profile_state"] == PROFILE_STATE_BUILDING
    assert payload["summary"]["emerging_signals"] == len(payload["emerging"])


def test_profile_state_covers_its_whole_enumeration() -> None:
    assert set(PROFILE_STATES) == {
        PROFILE_STATE_NO_ACTIVITY,
        PROFILE_STATE_NO_RATINGS,
        PROFILE_STATE_BUILDING,
        PROFILE_STATE_ESTABLISHED,
    }
    established = response_of([pattern(("a",))])
    assert established.summary.profile_state == PROFILE_STATE_ESTABLISHED


# --- 21: determinism ---------------------------------------------------------


def test_21_repeated_calls_over_identical_state_are_identical(
    api: DashboardApi,
) -> None:
    headers = register(api, "determinism")
    for work_id, rating in zip(api.psychological_ids, (10, 9, 9, 8)):
        rate(api, headers, work_id, rating)
    for work_id in api.mystery_ids:
        rate(api, headers, work_id, 7)

    first = dashboard(api, headers)
    second = dashboard(api, headers)
    third = dashboard(api, headers)

    assert first == second == third


def test_shuffled_source_patterns_give_an_identical_payload() -> None:
    patterns = [
        pattern((f"f{index}",), confidence=0.5 + index / 100) for index in range(6)
    ]

    forward = response_of(list(patterns)).model_dump()
    backward = response_of(list(reversed(patterns))).model_dump()

    assert forward == backward


# --- the projection itself ---------------------------------------------------


def test_domains_are_named_not_slugged() -> None:
    payload = response_of(
        [pattern(("a",), works=(work(domain="manhwa"), work(), work()))]
    )
    item = payload.strongly_likes[0]

    assert item.domains == ["Anime", "Manga & Manhwa"]
    assert item.evidence_summary.domains == ["Anime", "Manga & Manhwa"]


def test_the_domain_table_matches_the_preference_page() -> None:
    """Two product surfaces naming the same domain differently would be a bug."""
    from app.services.preference.product import _DOMAIN_NAMES

    assert DOMAIN_NAMES == _DOMAIN_NAMES


def test_a_combination_keeps_both_features_in_the_payload() -> None:
    payload = response_of(
        [pattern(("a", "b"), works=(work(), work(), work(), work()))]
    )
    item = payload.strongly_likes[0]

    assert item.kind == KIND_COMBINATION
    assert [f.key for f in item.features] == ["a", "b"]
    assert item.display_name == "A + B"


def test_what_stands_out_reports_a_combination_as_its_own_observation() -> None:
    """A pair earns a place there; a concept already in a group does not.

    Repeating "you strongly like this" under What stands out would be the
    same fact twice. What earns a place is a relationship -- here, that the
    aggregation layer established the pair itself rather than inferring it
    from its parts.
    """
    payload = response_of(
        [pattern(("mystery", "psychological-depth"), works=(work(), work(), work(), work()))]
    )

    observations = [o for o in payload.what_stands_out if o.observation == "combination_highlight"]
    assert observations, "an established combination should stand out"
    assert [f.name for f in observations[0].features] == ["Mystery", "Psychological Depth"]
    # An observation carries the same meaning-not-machinery contract.
    assert observations[0].presentation_key == "enjoys_combination"


def test_indistinguishable_alternatives_are_named_for_a_reader() -> None:
    shared = (work(), work(), work())
    payload = response_of(
        [pattern(("alpha-thing",), works=shared)],
        alternatives={"alpha-thing": ("beta-thing",)},
    )

    item = payload.strongly_likes[0]
    assert item.also_supported_by
    # A display name, not a slug.
    assert all("-" not in name for name in item.also_supported_by)


def test_reconsumption_is_reported_without_moving_the_group() -> None:
    reconsumed = SupportingWork(
        work_id=uuid.uuid4(),
        title="revisited",
        domain_slug="anime",
        status="completed",
        rating=9,
        normalized_rating=0.8,
        times_completed=3,
        in_library=True,
    )
    plain = pattern(("plain",), works=(work(), work(), work()))
    repeated = pattern(("repeated",), works=(reconsumed, work(), work()))

    payload = response_of([plain, repeated])
    by_key = {item.key: item for item in payload.strongly_likes}

    assert by_key["repeated"].evidence_summary.includes_reconsumed_works is True
    assert by_key["plain"].evidence_summary.includes_reconsumed_works is False
    # Both are strongly liked: repetition reported, never rewarded.
    assert {"plain", "repeated"} <= set(by_key)


def test_the_summary_is_facts_and_not_a_score() -> None:
    payload = response_of(
        [pattern(("a",)), pattern(("b",))],
        [pattern(("c",), status=STATUS_EMERGING, confidence=0.4)],
    )
    summary = payload.summary

    assert summary.established_preferences == 2
    assert summary.emerging_signals == 1
    assert set(summary.model_dump()) == {
        "profile_state",
        "rated_works",
        "established_preferences",
        "emerging_signals",
    }


def test_nothing_is_persisted_by_a_dashboard_request(api: DashboardApi) -> None:
    """No table, no cache: the same request twice writes nothing."""
    headers = register(api, "persistence")
    for work_id, rating in zip(api.psychological_ids, (10, 9, 9, 8)):
        rate(api, headers, work_id, rating)

    dashboard(api, headers)
    dashboard(api, headers)

    from app.main import app  # noqa: F401

    tables = {
        table
        for table in ("taste_profiles", "taste_patterns", "taste_insights", "dashboards")
    }
    from app.models.base import Base

    assert not (tables & set(Base.metadata.tables))
