"""The preference evidence endpoints, end to end through the app.

The isolation tests are the important ones: the endpoint takes its user from
the session and there is no parameter anywhere that names one, so a caller
cannot reach another person's evidence by editing a request.

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
from app.services import auth_service, library_service
from app.services.concepts.service import SourceLabel, apply_source_labels, ensure_vocabulary
from app.services.ingestion.anime import AniListAnimeAdapter
from app.services.ingestion.service import ingest_source_work

FIXTURES = Path(__file__).parent / "fixtures"
API_EMAIL_DOMAIN = "@preference-api.invalid"
API_SOURCE_IDS = (985001, 985002, 985003)
PASSWORD = "a-sufficiently-long-password"


@dataclass
class PreferenceApi:
    """A client plus the ids of the works this test seeded.

    The ids are carried explicitly rather than looked up by title: other
    suites create works with the same title, and matching on it made these
    tests pass alone and fail in a full run.
    """

    client: TestClient
    work_ids: list[str]


@pytest.fixture
def api(database_available: bool) -> Iterator[PreferenceApi]:
    """A client with three concept-carrying works, all removed afterwards."""
    if not database_available:
        pytest.skip("requires a live Postgres instance")

    from app.main import app

    seeded: list[str] = []

    async def seed() -> None:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(bind=engine, expire_on_commit=False)() as session:
                await ensure_vocabulary(session)
                media = json.loads(
                    (FIXTURES / "anilist_cowboy_bebop.json").read_text(encoding="utf-8")
                )
                for anilist_id in API_SOURCE_IDS:
                    payload = dict(media)
                    payload["id"] = anilist_id
                    payload["relations"] = {"edges": []}
                    payload["genres"] = []
                    payload["tags"] = []
                    result = await ingest_source_work(
                        session, AniListAnimeAdapter(media=payload).load()
                    )
                    work = await session.get(Work, result.work_id)
                    await apply_source_labels(
                        session,
                        work=work,
                        labels=[SourceLabel("Psychological", "anilist_tag", rank=88)],
                    )
                    seeded.append(str(work.id))
                await session.commit()
        finally:
            await engine.dispose()

    async def cleanup() -> None:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(bind=engine)() as session:
                refs = ", ".join(f"'{value}'" for value in API_SOURCE_IDS)
                work_ids = (
                    "SELECT id FROM works WHERE source = 'anilist' "
                    f"AND external_ids->>'source_ref' IN ({refs})"
                )
                for statement in (
                    f"DELETE FROM user_content_events WHERE interaction_id IN "
                    f"(SELECT id FROM user_content_interactions WHERE work_id IN ({work_ids}))",
                    f"DELETE FROM user_content_interactions WHERE work_id IN ({work_ids})",
                    f"DELETE FROM work_concepts WHERE work_id IN ({work_ids})",
                    f"DELETE FROM entities WHERE work_id IN ({work_ids})",
                    f"DELETE FROM containers WHERE work_id IN ({work_ids})",
                    f"DELETE FROM work_creators WHERE work_id IN ({work_ids})",
                    "DELETE FROM user_content_events WHERE interaction_id IN ("
                    " SELECT i.id FROM user_content_interactions i JOIN users u"
                    f" ON u.id = i.user_id WHERE u.email LIKE '%{API_EMAIL_DOMAIN}')",
                    "DELETE FROM user_content_interactions WHERE user_id IN "
                    f"(SELECT id FROM users WHERE email LIKE '%{API_EMAIL_DOMAIN}')",
                    "DELETE FROM user_sessions WHERE user_id IN "
                    f"(SELECT id FROM users WHERE email LIKE '%{API_EMAIL_DOMAIN}')",
                    f"DELETE FROM users WHERE email LIKE '%{API_EMAIL_DOMAIN}'",
                    f"DELETE FROM works WHERE id IN ({work_ids})",
                ):
                    await session.execute(text(statement))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed())
    with TestClient(app) as client:
        try:
            yield PreferenceApi(client=client, work_ids=seeded)
        finally:
            asyncio.run(cleanup())


def register(api: "PreferenceApi", name: str) -> dict:
    response = api.client.post(
        "/api/v1/auth/register",
        json={"email": f"{name}{API_EMAIL_DOMAIN}", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def build_history(api: "PreferenceApi", headers: dict, ratings: list[int | None]) -> None:
    for work_id, rating in zip(api.work_ids, ratings):
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


# --- authentication and isolation ----------------------------------------


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}]
)
def test_preference_evidence_requires_authentication(api: PreferenceApi, headers: dict) -> None:
    assert api.client.get("/api/v1/preferences", headers=headers).status_code == 401
    assert (
        api.client.get("/api/v1/preferences/psychological-depth", headers=headers).status_code == 401
    )


def test_the_endpoint_accepts_no_user_identifier_at_all(api: PreferenceApi) -> None:
    """Isolation is structural: there is no parameter to point elsewhere."""
    from app.main import app

    schema = app.openapi()["paths"]["/api/v1/preferences"]["get"]
    names = {parameter["name"] for parameter in schema.get("parameters", [])}

    assert "user_id" not in names
    assert not any("user" in name for name in names)


def test_two_users_with_the_same_works_receive_opposite_evidence(
    api: PreferenceApi,
) -> None:
    """The load-bearing case over the real HTTP path."""
    enthusiast = register(api, "enthusiast")
    detractor = register(api, "detractor")
    build_history(api, enthusiast, [9, 10, 9])
    build_history(api, detractor, [3, 4, 3])

    def evidence(headers):
        response = api.client.get("/api/v1/preferences/psychological-depth", headers=headers)
        assert response.status_code == 200
        return response.json()

    positive, negative = evidence(enthusiast), evidence(detractor)

    assert positive["works_completed"] == negative["works_completed"]
    assert positive["exposure"] == negative["exposure"]
    assert positive["direction"] == "positive"
    assert negative["direction"] == "negative"
    assert positive["preference_evidence"] > 0 > negative["preference_evidence"]


def test_one_user_never_sees_anothers_evidence(api: PreferenceApi) -> None:
    enthusiast = register(api, "enthusiast")
    stranger = register(api, "stranger")
    build_history(api, enthusiast, [9, 10, 9])

    profile = api.client.get("/api/v1/preferences", headers=stranger).json()

    assert profile["concepts"] == []
    assert profile["total_interactions"] == 0
    assert (
        api.client.get("/api/v1/preferences/psychological-depth", headers=stranger).status_code == 404
    )


# --- the response itself --------------------------------------------------


def test_the_profile_separates_counts_from_derived_signals(api: PreferenceApi) -> None:
    headers = register(api, "reader")
    build_history(api, headers, [9, 10, 8])

    body = api.client.get("/api/v1/preferences", headers=headers).json()
    evidence = next(
        item for item in body["concepts"] if item["concept_slug"] == "psychological-depth"
    )

    # Raw counts: what happened.
    assert evidence["works_exposed"] == 3
    assert evidence["works_completed"] == 3
    assert evidence["works_rated"] == 3
    assert sorted(evidence["ratings"]) == [8, 9, 10]
    # Derived signals: how this layer reads it, one channel each.
    for channel in ("exposure", "engagement", "rating_signal", "reconsumption_signal",
                    "abandonment_signal"):
        assert channel in evidence
    # Direction and confidence, kept apart.
    assert evidence["direction"] == "positive"
    assert 0.0 < evidence["confidence"] < 1.0


def test_the_response_explains_itself(api: PreferenceApi) -> None:
    headers = register(api, "reader")
    build_history(api, headers, [9, 10, None])

    evidence = api.client.get("/api/v1/preferences/psychological-depth", headers=headers).json()

    assert len(evidence["contributions"]) == 3
    rated = [item for item in evidence["contributions"] if item["rating"] is not None]
    assert len(rated) == 2
    for contribution in rated:
        assert contribution["normalized_rating"] is not None
        assert uuid.UUID(contribution["work_id"])
        assert contribution["title"]


def test_the_rating_context_is_reported_with_its_reliability(api: PreferenceApi) -> None:
    headers = register(api, "reader")
    build_history(api, headers, [9, 9, 9])

    context = api.client.get("/api/v1/preferences", headers=headers).json()["rating_context"]

    assert context["rating_count"] == 3
    assert context["observed_mean"] == 9.0
    assert 5.5 < context["baseline"] < 9.0  # shrunk toward the scale midpoint
    assert 0.0 < context["normalization_confidence"] < 1.0


def test_unrated_completions_report_unknown_not_positive(api: PreferenceApi) -> None:
    headers = register(api, "silent")
    build_history(api, headers, [None, None, None])

    evidence = api.client.get("/api/v1/preferences/psychological-depth", headers=headers).json()

    assert evidence["works_completed"] == 3
    assert evidence["engagement"] > 0
    assert evidence["preference_evidence"] is None
    assert evidence["direction"] == "unknown"
    assert evidence["confidence"] == 0.0


def test_a_user_with_no_history_gets_an_empty_profile(api: PreferenceApi) -> None:
    headers = register(api, "newcomer")

    body = api.client.get("/api/v1/preferences", headers=headers).json()

    assert body["concepts"] == []
    assert body["total_interactions"] == 0
    assert body["rating_context"]["rating_count"] == 0


def test_concepts_can_be_filtered_to_those_with_rating_support(api: PreferenceApi) -> None:
    headers = register(api, "reader")
    build_history(api, headers, [9, None, None])

    everything = api.client.get("/api/v1/preferences", headers=headers).json()["concepts"]
    supported = api.client.get(
        "/api/v1/preferences", params={"min_rated": 1}, headers=headers
    ).json()["concepts"]

    assert len(supported) <= len(everything)
    assert all(item["works_rated"] >= 1 for item in supported)


def test_an_unknown_concept_is_404_not_an_invented_zero(api: PreferenceApi) -> None:
    headers = register(api, "reader")
    build_history(api, headers, [9, 9, 9])

    response = api.client.get("/api/v1/preferences/not-a-real-concept", headers=headers)

    assert response.status_code == 404


def test_the_response_exposes_no_corpus_internals(api: PreferenceApi) -> None:
    headers = register(api, "reader")
    build_history(api, headers, [9, 10, 8])

    raw = api.client.get("/api/v1/preferences", headers=headers).text

    for forbidden in (
        "text_content",
        "content_unit",
        "embedding",
        "contextual_passage",
        "supporting_labels",
        "extra_metadata",
        "source_hash",
        "adapter",
    ):
        assert forbidden not in raw


def test_the_response_makes_no_personality_claim(api: PreferenceApi) -> None:
    """This layer reports media preference evidence, and says so."""
    headers = register(api, "reader")
    build_history(api, headers, [9, 10, 8])

    raw = api.client.get("/api/v1/preferences", headers=headers).text.lower()

    for forbidden in (
        "personality",
        "trait",
        "introvert",
        "extrovert",
        "neurotic",
        "openness",
        "you are",
    ):
        assert forbidden not in raw


# --- the product overview endpoint ---------------------------------------


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}]
)
def test_the_overview_requires_authentication(api: PreferenceApi, headers: dict) -> None:
    assert api.client.get("/api/v1/preferences/overview", headers=headers).status_code == 401


def test_the_overview_accepts_no_user_identifier(api: PreferenceApi) -> None:
    from app.main import app

    schema = app.openapi()["paths"]["/api/v1/preferences/overview"]["get"]
    names = {parameter["name"] for parameter in schema.get("parameters", [])}

    assert names == set()


def test_the_overview_never_returns_another_users_evidence(api: PreferenceApi) -> None:
    reader = register(api, "reader")
    stranger = register(api, "stranger")
    build_history(api, reader, [9, 10, 9])

    body = api.client.get("/api/v1/preferences/overview", headers=stranger).json()

    assert body["signals"] == []
    assert body["awaiting_ratings"] == []
    assert body["summary"]["total_interactions"] == 0


def test_the_overview_exposes_no_engine_internals(api: PreferenceApi) -> None:
    headers = register(api, "reader")
    build_history(api, headers, [9, 10, 8])

    raw = api.client.get("/api/v1/preferences/overview", headers=headers).text

    for forbidden in (
        "baseline",
        "spread",
        "normalized_rating",
        "preference_evidence",
        "rating_signal",
        "concept_confidence",
        "supporting_labels",
        "user_id",
        "content_unit",
        "embedding",
    ):
        assert forbidden not in raw


def test_the_overview_makes_no_personality_or_recommendation_claim(
    api: PreferenceApi,
) -> None:
    headers = register(api, "reader")
    build_history(api, headers, [9, 10, 8])

    raw = api.client.get("/api/v1/preferences/overview", headers=headers).text.lower()

    for forbidden in ("personality", "trait", "introvert", "recommend", "you should", "you are"):
        assert forbidden not in raw


def test_unrated_completions_arrive_in_their_own_list(api: PreferenceApi) -> None:
    """The structural guarantee: engagement cannot be skim-read as approval."""
    headers = register(api, "silent")
    build_history(api, headers, [None, None, None])

    body = api.client.get("/api/v1/preferences/overview", headers=headers).json()

    assert body["signals"] == []
    assert body["awaiting_ratings"]
    assert body["summary"]["works_rated"] == 0


def test_the_overview_reports_confidence_as_a_band_not_a_number(
    api: PreferenceApi,
) -> None:
    headers = register(api, "reader")
    build_history(api, headers, [9, 10, 8])

    body = api.client.get("/api/v1/preferences/overview", headers=headers).json()

    assert body["signals"]
    for signal in body["signals"]:
        assert signal["confidence_band"] in {"low", "moderate", "high"}
        assert "confidence" not in signal
