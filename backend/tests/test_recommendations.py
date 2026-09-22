"""Content-based discovery: the first layer that suggests something.

Three things are under test, and they fail in different ways.

The **scoring rules**, which are pure and are tested without a database. The
one that matters most is that a concept contributes once however many
patterns mention it and however many `work_concepts` rows carry it -- a work
with eighteen tags must not outrank a work with four because of the count.

The **chain**, end to end against a real corpus: a rating becomes an
established preference, the preference names concepts, the concepts select
catalogue works, and the explanation that comes back names the preference. A
recommendation whose stated reason is not actually on the work is the failure
this whole design exists to prevent.

The **boundary**, which is the same one every user-scoped route uses: the
reader comes from the session, no parameter names one, and an anonymous
caller gets 401 rather than a generic shelf.

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
from app.services.concepts.service import (
    SourceLabel,
    apply_source_labels,
    ensure_vocabulary,
)
from app.services.ingestion.anime import AniListAnimeAdapter
from app.services.ingestion.service import ingest_source_work
from app.services.preference.dashboard import (
    EvidenceSummary,
    FeatureRef,
    PreferenceItem,
)
from app.services.preference.evidence import DIRECTION_NEGATIVE, DIRECTION_POSITIVE
from app.services.preference.insights import (
    PRESENTATION_ENJOYS_COMBINATION,
    PRESENTATION_ENJOYS_FEATURE,
)
from app.services.preference.taste import KIND_COMBINATION, KIND_INDIVIDUAL
from app.services.recommendation import (
    DEFAULT_DIVERSITY,
    STATE_BUILDING,
    STATE_NO_ACTIVITY,
    STATE_NO_MATCHES,
    STATE_NO_RATINGS,
    STATE_PERSONALIZED,
    STATES,
    DiversityRule,
    Recommendation,
    RecommendationReason,
    ReasonConcept,
    _rules,
    apply_diversity,
    score_candidate,
)

FIXTURES = Path(__file__).parent / "fixtures"
REC_EMAIL_DOMAIN = "@recommendation-api.invalid"
PASSWORD = "a-sufficiently-long-password"

# A block of AniList ids no other suite uses, so a full run cannot collide.
#
# The shape of the fixture corpus:
#   4 rated "Psychological" works -> an established positive preference
#   4 rated "Horror" works        -> an established negative preference
#   2 rated "Mystery" works       -> emerging only: two ratings is the minimum
#                                    support, which is discovery, not settled
#   the rest are unrated candidates carrying known combinations of those
#   concepts.
#
# Every one of them is ingested with no episodes, so none has a container, a
# content unit or an embedding. That is deliberate: it makes "a metadata-only
# work is still recommendable" the default case here rather than a special
# one.
LIKED = (985001, 985002, 985003, 985004)
DISLIKED = (985011, 985012, 985013, 985014)
EMERGING = (985021, 985022)

CAND_LIKED_ONLY = 985031
CAND_LIKED_TOO = 985032
CAND_LIKED_AND_DISLIKED = 985033
CAND_DISLIKED_ONLY = 985034
CAND_UNRELATED = 985035
CAND_EMERGING_ONLY = 985036
CAND_LIKED_THIRD = 985037
CAND_LIKED_FOURTH = 985038

CANDIDATES = (
    CAND_LIKED_ONLY,
    CAND_LIKED_TOO,
    CAND_LIKED_AND_DISLIKED,
    CAND_DISLIKED_ONLY,
    CAND_UNRELATED,
    CAND_EMERGING_ONLY,
    CAND_LIKED_THIRD,
    CAND_LIKED_FOURTH,
)

ALL_IDS = LIKED + DISLIKED + EMERGING + CANDIDATES

# Source labels, which the concept vocabulary maps onto canonical concepts.
LABELS: dict[int, tuple[str, ...]] = {
    **{anilist_id: ("Psychological",) for anilist_id in LIKED},
    **{anilist_id: ("Horror",) for anilist_id in DISLIKED},
    **{anilist_id: ("Mystery",) for anilist_id in EMERGING},
    CAND_LIKED_ONLY: ("Psychological",),
    CAND_LIKED_TOO: ("Psychological",),
    CAND_LIKED_AND_DISLIKED: ("Psychological", "Horror"),
    CAND_DISLIKED_ONLY: ("Horror",),
    CAND_UNRELATED: ("Sports",),
    CAND_EMERGING_ONLY: ("Mystery",),
    CAND_LIKED_THIRD: ("Psychological",),
    CAND_LIKED_FOURTH: ("Psychological",),
}


# --- the scoring rules, without a database --------------------------------


def item(
    *keys: str,
    direction: str = DIRECTION_POSITIVE,
    evidence: float = 0.6,
    confidence: float = 0.5,
    band: str = "moderate",
    rated: int = 4,
) -> PreferenceItem:
    """One established dashboard item, as the recommender receives it."""
    features = tuple(FeatureRef(key=key, name=key.title(), family="theme") for key in keys)
    return PreferenceItem(
        key="+".join(keys),
        display_name=" + ".join(key.title() for key in keys),
        features=features,
        kind=KIND_INDIVIDUAL if len(keys) == 1 else KIND_COMBINATION,
        direction=direction,
        bucket="strongly_likes",
        confidence_band=band,
        presentation_key=(
            PRESENTATION_ENJOYS_COMBINATION if len(keys) > 1 else PRESENTATION_ENJOYS_FEATURE
        ),
        evidence=EvidenceSummary(
            works_rated=rated,
            works_completed=rated,
            works_exposed=rated,
            rating_mean=9.0,
            preference_evidence=evidence if direction == DIRECTION_POSITIVE else -evidence,
            confidence=confidence,
        ),
    )


def test_a_matched_preference_supports_the_work() -> None:
    result = score_candidate(frozenset({"psychological"}), _rules([item("psychological")]))

    assert result.score == pytest.approx(0.6 * 0.5)
    assert [rule.item.key for rule in result.accepted_positive] == ["psychological"]


def test_an_unmatched_preference_contributes_nothing() -> None:
    result = score_candidate(frozenset({"sports"}), _rules([item("psychological")]))

    assert result.score == 0.0
    assert result.accepted_positive == []


def test_a_negative_preference_subtracts() -> None:
    rules = _rules(
        [item("psychological"), item("horror", direction=DIRECTION_NEGATIVE)]
    )

    both = score_candidate(frozenset({"psychological", "horror"}), rules)
    only_liked = score_candidate(frozenset({"psychological"}), rules)

    assert both.score < only_liked.score
    assert [rule.item.key for rule in both.accepted_negative] == ["horror"]


def test_confidence_weighs_two_equally_strong_likings_apart() -> None:
    """The one arithmetic step: strength times how well attested it is."""
    well_attested = score_candidate(
        frozenset({"a"}), _rules([item("a", evidence=0.6, confidence=0.8)])
    )
    barely = score_candidate(
        frozenset({"a"}), _rules([item("a", evidence=0.6, confidence=0.4)])
    )

    assert well_attested.score > barely.score


def test_a_concept_contributes_once_however_many_patterns_name_it() -> None:
    """A combination and its part must not both be paid for the same concept.

    A reader can hold `psychological` and `psychological + mystery` at the
    same time. A work carrying both concepts matches both patterns, and
    paying for `psychological` twice would make it outrank a work that
    genuinely matches two different preferences.
    """
    rules = _rules(
        [
            item("psychological", evidence=0.6, confidence=0.5),
            item("psychological", "mystery", evidence=0.8, confidence=0.5),
        ]
    )

    result = score_candidate(frozenset({"psychological", "mystery"}), rules)

    # The stronger pattern wins the concepts and the weaker one is not added
    # on top: 0.8 * 0.5, not 0.8 * 0.5 + 0.6 * 0.5.
    assert result.score == pytest.approx(0.8 * 0.5)
    assert [rule.item.key for rule in result.accepted_positive] == ["psychological+mystery"]


def test_two_genuinely_different_preferences_do_both_count() -> None:
    """The rule above is about double-paying, not about capping support."""
    rules = _rules(
        [
            item("psychological", evidence=0.6, confidence=0.5),
            item("tragedy", evidence=0.4, confidence=0.5),
        ]
    )

    result = score_candidate(frozenset({"psychological", "tragedy"}), rules)

    assert result.score == pytest.approx(0.6 * 0.5 + 0.4 * 0.5)
    assert len(result.accepted_positive) == 2


def test_a_combination_needs_both_of_its_concepts() -> None:
    """What the pattern claims is the pair, so half of it is not a match."""
    rules = _rules([item("psychological", "mystery")])

    half = score_candidate(frozenset({"psychological"}), rules)
    whole = score_candidate(frozenset({"psychological", "mystery"}), rules)

    assert half.accepted_positive == []
    assert len(whole.accepted_positive) == 1


def test_scoring_reads_a_set_so_repeated_associations_cannot_multiply() -> None:
    """`work_concepts` is unique per pair, and the scorer relies on a set.

    Belt and braces: even handed the same concept repeatedly, the score is
    the score for carrying it once.
    """
    rules = _rules([item("psychological")])
    once = score_candidate(frozenset({"psychological"}), rules)
    with_noise = score_candidate(
        frozenset({"psychological", "unrelated-a", "unrelated-b", "unrelated-c"}), rules
    )

    assert with_noise.score == once.score


# --- diversity -------------------------------------------------------------


def recommendation(
    title: str, *, score: float, concept: str, domain: str = "anime"
) -> Recommendation:
    reason = RecommendationReason(
        presentation_key=PRESENTATION_ENJOYS_FEATURE,
        direction=DIRECTION_POSITIVE,
        concepts=(ReasonConcept(key=concept, name=concept.title()),),
        confidence_band="moderate",
        rated_works=4,
    )
    return Recommendation(
        work_id=uuid.uuid5(uuid.NAMESPACE_OID, title),
        title=title,
        domain_slug=domain,
        reasons=(reason,),
        cautions=(),
        confidence_band="moderate",
        score=score,
    )


DOMAINS = ("anime", "literature", "manhwa")


def test_one_concept_does_not_fill_the_front_of_the_shelf() -> None:
    """The failure this rule exists for: four near-duplicates in a row.

    Domains are varied so that only the concept cap is under test here; the
    medium cap has its own case below.
    """
    ranked = [
        recommendation(
            f"psych-{index}",
            score=1.0 - index / 100,
            concept="psychological",
            domain=DOMAINS[index % 3],
        )
        for index in range(4)
    ] + [recommendation("other", score=0.5, concept="tragedy", domain="literature")]

    shelf = apply_diversity(ranked, limit=4)

    leading = [rec.leading_concept_key for rec in shelf]
    assert leading[:3] == ["psychological", "psychological", "tragedy"]


def test_one_medium_does_not_crowd_out_the_others() -> None:
    """Five anime outrank one novel; the novel still reaches the shelf."""
    ranked = [
        recommendation(f"anime-{index}", score=1.0 - index / 100, concept=f"c{index}")
        for index in range(5)
    ] + [recommendation("book", score=0.4, concept="c9", domain="literature")]

    shelf = apply_diversity(ranked, limit=4)
    domains = [rec.domain_slug for rec in shelf]

    # The cap holds while the other medium still has a candidate...
    assert domains[:3] == ["anime", "anime", "literature"]
    # ...and then relaxes rather than leaving the shelf short.
    assert len(shelf) == 4


def test_a_narrow_taste_still_fills_the_shelf() -> None:
    """Spacing, not filtering. Held-back candidates fill the remaining slots.

    A reader with one established preference must not be shown two results
    because of a diversity rule.
    """
    ranked = [
        recommendation(f"psych-{index}", score=1.0 - index / 100, concept="psychological")
        for index in range(6)
    ]

    shelf = apply_diversity(ranked, limit=5)

    assert len(shelf) == 5


def test_diversity_is_deterministic() -> None:
    ranked = [
        recommendation(f"w{index}", score=1.0 - index / 100, concept=f"c{index % 3}")
        for index in range(12)
    ]

    first = [rec.title for rec in apply_diversity(ranked, limit=6)]
    second = [rec.title for rec in apply_diversity(ranked, limit=6)]

    assert first == second


def test_the_diversity_rule_is_a_parameter_not_a_literal() -> None:
    ranked = [
        recommendation(f"psych-{index}", score=1.0 - index / 100, concept="psychological")
        for index in range(4)
    ] + [recommendation("other", score=0.5, concept="tragedy")]

    relaxed = apply_diversity(ranked, limit=4, rule=DiversityRule(max_per_leading_concept=4))

    assert [rec.leading_concept_key for rec in relaxed] == ["psychological"] * 4
    assert DEFAULT_DIVERSITY.max_per_leading_concept == 2


# --- the chain, end to end ------------------------------------------------


@dataclass
class RecommendationApi:
    client: TestClient
    work_ids: dict[int, str]

    def id(self, anilist_id: int) -> str:
        return self.work_ids[anilist_id]


@pytest.fixture
def api(database_available: bool) -> Iterator[RecommendationApi]:
    if not database_available:
        pytest.skip("requires a live Postgres instance")

    from app.main import app

    work_ids: dict[int, str] = {}

    async def seed() -> None:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(bind=engine, expire_on_commit=False)() as session:
                await ensure_vocabulary(session)
                media = json.loads(
                    (FIXTURES / "anilist_cowboy_bebop.json").read_text(encoding="utf-8")
                )
                for anilist_id in ALL_IDS:
                    payload = dict(media)
                    payload["id"] = anilist_id
                    payload["title"] = {
                        "romaji": f"Rec Work {anilist_id}",
                        "english": f"Rec Work {anilist_id}",
                        "native": f"Rec Work {anilist_id}",
                    }
                    payload["relations"] = {"edges": []}
                    payload["genres"] = []
                    payload["tags"] = []
                    # No episodes, so no containers, no content units and no
                    # embeddings. Every work here is metadata-only.
                    payload["episodes"] = None
                    payload["streamingEpisodes"] = []
                    result = await ingest_source_work(
                        session, AniListAnimeAdapter(media=payload).load()
                    )
                    work = await session.get(Work, result.work_id)
                    await apply_source_labels(
                        session,
                        work=work,
                        labels=[
                            SourceLabel(name, "anilist_tag", rank=88)
                            for name in LABELS[anilist_id]
                        ],
                    )
                    work_ids[anilist_id] = str(work.id)
                await session.commit()
        finally:
            await engine.dispose()

    async def cleanup() -> None:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(bind=engine)() as session:
                refs = ", ".join(f"'{value}'" for value in ALL_IDS)
                works = (
                    "SELECT id FROM works WHERE source = 'anilist' "
                    f"AND external_ids->>'source_ref' IN ({refs})"
                )
                users = f"SELECT id FROM users WHERE email LIKE '%{REC_EMAIL_DOMAIN}'"
                for statement in (
                    "DELETE FROM user_content_events WHERE interaction_id IN "
                    f"(SELECT id FROM user_content_interactions WHERE user_id IN ({users}))",
                    f"DELETE FROM user_content_interactions WHERE user_id IN ({users})",
                    "DELETE FROM user_content_events WHERE interaction_id IN "
                    f"(SELECT id FROM user_content_interactions WHERE work_id IN ({works}))",
                    f"DELETE FROM user_content_interactions WHERE work_id IN ({works})",
                    f"DELETE FROM work_concepts WHERE work_id IN ({works})",
                    f"DELETE FROM entities WHERE work_id IN ({works})",
                    f"DELETE FROM containers WHERE work_id IN ({works})",
                    f"DELETE FROM work_creators WHERE work_id IN ({works})",
                    "DELETE FROM user_preference_feedback_events WHERE feedback_id IN "
                    f"(SELECT id FROM user_preference_feedback WHERE user_id IN ({users}))",
                    f"DELETE FROM user_preference_feedback WHERE user_id IN ({users})",
                    f"DELETE FROM user_sessions WHERE user_id IN ({users})",
                    f"DELETE FROM users WHERE email LIKE '%{REC_EMAIL_DOMAIN}'",
                    f"DELETE FROM works WHERE id IN ({works})",
                ):
                    await session.execute(text(statement))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed())
    with TestClient(app) as client:
        try:
            yield RecommendationApi(client=client, work_ids=work_ids)
        finally:
            asyncio.run(cleanup())


def register(api: RecommendationApi, name: str) -> dict:
    response = api.client.post(
        "/api/v1/auth/register",
        json={"email": f"{name}{REC_EMAIL_DOMAIN}", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def rate(api: RecommendationApi, headers: dict, anilist_id: int, rating: int | None) -> None:
    work_id = api.id(anilist_id)
    api.client.post("/api/v1/library", json={"work_id": work_id}, headers=headers)
    api.client.patch(
        f"/api/v1/library/{work_id}", json={"status": STATUS_COMPLETED}, headers=headers
    )
    if rating is not None:
        api.client.patch(
            f"/api/v1/library/{work_id}",
            json={"rating": rating, "rating_set": True},
            headers=headers,
        )


# The fixture corpus lives in the same database as the real one, so a reader
# whose preference is "psychological" matches real works too. Membership
# assertions therefore ask for the whole shelf rather than the first page --
# what is under test is whether a work is selected at all, not where a
# fixture work lands among sixty real ones.
WHOLE_SHELF = 50


def recommendations(api: RecommendationApi, headers: dict, **params) -> dict:
    response = api.client.get(
        "/api/v1/recommendations", headers=headers, params=params or None
    )
    assert response.status_code == 200, response.text
    return response.json()


def shelf(api: RecommendationApi, headers: dict) -> dict:
    return recommendations(api, headers, limit=WHOLE_SHELF)


def established_reader(api: RecommendationApi, name: str = "reader") -> dict:
    """Four highly rated Psychological works: one established positive."""
    headers = register(api, name)
    for anilist_id in LIKED:
        rate(api, headers, anilist_id, 9)
    return headers


def titles(payload: dict) -> list[str]:
    return [rec["work"]["title"] for rec in payload["recommendations"]]


def work_ids_in(payload: dict) -> list[str]:
    return [rec["work"]["id"] for rec in payload["recommendations"]]


# --- 1, 4, 14, 15 ---------------------------------------------------------


def test_an_established_reader_receives_recommendations(api: RecommendationApi) -> None:
    headers = established_reader(api)

    payload = recommendations(api, headers)

    assert payload["summary"]["state"] == STATE_PERSONALIZED
    assert payload["recommendations"]
    assert payload["summary"]["established_preferences"] >= 1


def test_positive_preferences_select_the_works_that_carry_them(
    api: RecommendationApi,
) -> None:
    headers = established_reader(api)

    ids = work_ids_in(shelf(api, headers))

    assert api.id(CAND_LIKED_ONLY) in ids
    assert api.id(CAND_UNRELATED) not in ids


def test_a_metadata_only_work_with_no_embedding_is_still_recommendable(
    api: RecommendationApi,
) -> None:
    """The point of building this on concepts rather than on vectors.

    Every work in this fixture has no episodes, so none has a container, a
    content unit or an embedding -- and they are recommended anyway.
    """
    headers = established_reader(api)

    ids = work_ids_in(shelf(api, headers))

    # Four of the fixture's candidates carry the liked concept, and not one of
    # them has a container, a content unit or an embedding.
    for anilist_id in (
        CAND_LIKED_ONLY,
        CAND_LIKED_TOO,
        CAND_LIKED_THIRD,
        CAND_LIKED_FOURTH,
    ):
        assert api.id(anilist_id) in ids


# --- 2, 11 ----------------------------------------------------------------


def test_an_anonymous_caller_is_refused(api: RecommendationApi) -> None:
    """No generic shelf. There is no evidence without a reader."""
    response = api.client.get("/api/v1/recommendations")

    assert response.status_code == 401


def test_a_bad_token_is_refused(api: RecommendationApi) -> None:
    response = api.client.get(
        "/api/v1/recommendations", headers={"Authorization": "Bearer not-a-token"}
    )

    assert response.status_code == 401


def test_no_user_id_parameter_can_redirect_the_shelf(api: RecommendationApi) -> None:
    """The isolation boundary: the reader comes from the session, only."""
    mine = established_reader(api, "isolation-owner")
    other = register(api, "isolation-other")
    for anilist_id in DISLIKED:
        rate(api, other, anilist_id, 9)

    other_user_id = api.client.get("/api/v1/auth/me", headers=other).json()["id"]
    baseline = work_ids_in(recommendations(api, mine))

    for params in (
        {"user_id": other_user_id},
        {"user": other_user_id},
        {"userId": other_user_id},
    ):
        assert work_ids_in(recommendations(api, mine, **params)) == baseline


# --- 3 --------------------------------------------------------------------


def test_works_already_in_the_library_are_never_recommended(
    api: RecommendationApi,
) -> None:
    headers = established_reader(api)

    ids = work_ids_in(shelf(api, headers))

    for anilist_id in LIKED:
        assert api.id(anilist_id) not in ids


def test_a_removed_work_stays_out(api: RecommendationApi) -> None:
    """Removing something is the clearest available "stop showing me this"."""
    headers = established_reader(api, "remover")
    candidate = api.id(CAND_LIKED_ONLY)
    assert candidate in work_ids_in(shelf(api, headers))

    api.client.post("/api/v1/library", json={"work_id": candidate}, headers=headers)
    api.client.delete(f"/api/v1/library/{candidate}", headers=headers)

    assert candidate not in work_ids_in(shelf(api, headers))


# --- 5 --------------------------------------------------------------------


def test_a_mild_dislike_lowers_a_work_and_is_declared_beside_it(
    api: RecommendationApi,
) -> None:
    """The penalty is a ranking signal first, and an honest one.

    A work carrying a liked concept and a mildly disliked one is still a
    discovery -- it is ranked below the work that carries only the liked
    concept, and the reason it is lower travels with it rather than being
    quietly applied.
    """
    headers = register(api, "mixed-mild")
    for anilist_id in LIKED:
        rate(api, headers, anilist_id, 10)
    for anilist_id in DISLIKED:
        rate(api, headers, anilist_id, 4)

    payload = shelf(api, headers)
    ids = work_ids_in(payload)

    assert api.id(CAND_LIKED_ONLY) in ids
    assert api.id(CAND_LIKED_AND_DISLIKED) in ids
    assert ids.index(api.id(CAND_LIKED_ONLY)) < ids.index(api.id(CAND_LIKED_AND_DISLIKED))

    mixed = next(
        rec
        for rec in payload["recommendations"]
        if rec["work"]["id"] == api.id(CAND_LIKED_AND_DISLIKED)
    )
    assert mixed["cautions"]
    assert all(caution["direction"] == DIRECTION_NEGATIVE for caution in mixed["cautions"])
    assert not next(
        rec for rec in payload["recommendations"] if rec["work"]["id"] == api.id(CAND_LIKED_ONLY)
    )["cautions"]


def test_a_decisive_dislike_removes_the_work_entirely(api: RecommendationApi) -> None:
    """Strongly disliked outweighs liked, and the work leaves the shelf."""
    headers = register(api, "mixed-decisive")
    for anilist_id in LIKED:
        rate(api, headers, anilist_id, 9)
    for anilist_id in DISLIKED:
        rate(api, headers, anilist_id, 2)

    ids = work_ids_in(shelf(api, headers))

    assert api.id(CAND_LIKED_ONLY) in ids
    assert api.id(CAND_LIKED_AND_DISLIKED) not in ids


def test_a_work_matching_only_a_dislike_is_not_recommended(
    api: RecommendationApi,
) -> None:
    headers = register(api, "dislikes-only")
    for anilist_id in LIKED:
        rate(api, headers, anilist_id, 9)
    for anilist_id in DISLIKED:
        rate(api, headers, anilist_id, 2)

    ids = work_ids_in(shelf(api, headers))

    assert api.id(CAND_DISLIKED_ONLY) not in ids


# --- 6 --------------------------------------------------------------------


def test_an_emerging_preference_does_not_produce_recommendations(
    api: RecommendationApi,
) -> None:
    """Two ratings is the minimum support, which is discovery, not settled.

    The established preference selects works; the emerging one selects
    nothing, so it cannot outweigh anything however strong its evidence.
    """
    headers = register(api, "emerging")
    for anilist_id in LIKED:
        rate(api, headers, anilist_id, 9)
    for anilist_id in EMERGING:
        rate(api, headers, anilist_id, 10)

    ids = work_ids_in(shelf(api, headers))

    assert api.id(CAND_LIKED_ONLY) in ids
    assert api.id(CAND_EMERGING_ONLY) not in ids


# --- 8 --------------------------------------------------------------------


def test_every_stated_reason_is_a_concept_the_work_actually_carries(
    api: RecommendationApi,
) -> None:
    """The failure this design exists to prevent.

    A recommendation whose explanation names a concept that is not on the
    work would be a fabricated reason, however good the ranking was.
    """
    headers = established_reader(api, "evidence")
    payload = recommendations(api, headers)
    assert payload["recommendations"]

    for rec in payload["recommendations"]:
        concepts = api.client.get(f"/api/v1/works/{rec['work']['id']}/concepts")
        assert concepts.status_code == 200
        carried = {entry["slug"] for entry in concepts.json()}
        for reason in rec["reasons"] + rec["cautions"]:
            for concept in reason["concepts"]:
                assert concept["key"] in carried, (
                    f"{rec['work']['title']} was explained by {concept['key']}, "
                    "which it does not carry"
                )


def test_reasons_carry_a_presentation_key_rather_than_a_sentence(
    api: RecommendationApi,
) -> None:
    """The wording is the client's. A backend shipping prose makes the claim."""
    headers = established_reader(api, "wording")
    payload = recommendations(api, headers)

    for rec in payload["recommendations"]:
        for reason in rec["reasons"]:
            assert reason["presentation_key"] in {
                PRESENTATION_ENJOYS_FEATURE,
                PRESENTATION_ENJOYS_COMBINATION,
            }
            assert reason["confidence_band"] in {"low", "moderate", "high"}
            assert reason["rated_works"] >= 1


def test_no_scoring_internal_reaches_the_client(api: RecommendationApi) -> None:
    """A product explanation, not a ranking dump."""
    headers = established_reader(api, "contract")
    payload = recommendations(api, headers)

    forbidden = {
        "score",
        "preference_evidence",
        "confidence",
        "support",
        "penalty",
        "contribution",
        "normalized_rating",
        "weight",
        "baseline",
    }

    def walk(node, path="") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in forbidden, f"{path}.{key} exposes a scoring internal"
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for entry in node:
                walk(entry, path)

    walk(payload)


# --- 9 --------------------------------------------------------------------


def test_a_reader_with_no_activity_gets_an_honest_state(api: RecommendationApi) -> None:
    headers = register(api, "cold-none")

    payload = recommendations(api, headers)

    assert payload["summary"]["state"] == STATE_NO_ACTIVITY
    assert payload["recommendations"] == []


def test_a_reader_who_has_rated_nothing_gets_an_honest_state(
    api: RecommendationApi,
) -> None:
    headers = register(api, "cold-unrated")
    rate(api, headers, LIKED[0], None)

    payload = recommendations(api, headers)

    assert payload["summary"]["state"] == STATE_NO_RATINGS
    assert payload["recommendations"] == []


def test_a_reader_whose_evidence_has_not_settled_gets_an_honest_state(
    api: RecommendationApi,
) -> None:
    """Two ratings is not a taste. Nothing is invented to fill the shelf."""
    headers = register(api, "cold-building")
    rate(api, headers, LIKED[0], 9)

    payload = recommendations(api, headers)

    assert payload["summary"]["state"] == STATE_BUILDING
    assert payload["recommendations"] == []


def test_every_state_is_one_of_the_declared_set(api: RecommendationApi) -> None:
    for name, build in (
        ("state-none", lambda h: None),
        ("state-one", lambda h: rate(api, h, LIKED[0], 9)),
        ("state-full", lambda h: [rate(api, h, i, 9) for i in LIKED]),
    ):
        headers = register(api, name)
        build(headers)
        assert recommendations(api, headers)["summary"]["state"] in STATES


# --- 10, 12 ---------------------------------------------------------------


def test_two_readers_receive_results_from_their_own_evidence(
    api: RecommendationApi,
) -> None:
    likes_psychological = register(api, "reader-one")
    for anilist_id in LIKED:
        rate(api, likes_psychological, anilist_id, 9)

    likes_horror = register(api, "reader-two")
    for anilist_id in DISLIKED:
        rate(api, likes_horror, anilist_id, 9)

    first = work_ids_in(shelf(api, likes_psychological))
    second = work_ids_in(shelf(api, likes_horror))

    assert first != second
    assert api.id(CAND_LIKED_ONLY) in first
    assert api.id(CAND_DISLIKED_ONLY) in second
    assert api.id(CAND_DISLIKED_ONLY) not in first


def test_the_same_request_twice_returns_the_same_shelf(api: RecommendationApi) -> None:
    """An ordering that changes between identical requests is not an ordering."""
    headers = established_reader(api, "determinism")

    first = work_ids_in(recommendations(api, headers))
    second = work_ids_in(recommendations(api, headers))
    third = work_ids_in(recommendations(api, headers))

    assert first == second == third
    assert first


def test_ties_resolve_on_a_stable_key(api: RecommendationApi) -> None:
    """Five candidates carry exactly one concept and score identically."""
    headers = established_reader(api, "ties")

    payload = shelf(api, headers)
    fixture_titles = [
        rec["work"]["title"]
        for rec in payload["recommendations"]
        if rec["work"]["title"].startswith("Rec Work ")
    ]

    assert len(fixture_titles) >= 4
    assert fixture_titles == sorted(fixture_titles), "equal scores order by title"


# --- limits ---------------------------------------------------------------


def test_the_shelf_can_be_asked_for_fewer(api: RecommendationApi) -> None:
    headers = established_reader(api, "limited")

    assert len(recommendations(api, headers, limit=2)["recommendations"]) == 2


def test_an_absurd_limit_is_refused(api: RecommendationApi) -> None:
    headers = established_reader(api, "absurd")

    assert (
        api.client.get(
            "/api/v1/recommendations", headers=headers, params={"limit": 5000}
        ).status_code
        == 422
    )
