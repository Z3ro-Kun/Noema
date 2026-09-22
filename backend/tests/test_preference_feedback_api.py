"""Phase 1X: what a reader says back about Noema's reading of their taste.

The distinction every test here defends is that explicit feedback is **not**
a rating and **not** a preference:

    behavioural evidence     the reader completed and rated works
    inferred preference      Noema's reading of those ratings
    explicit feedback        the reader's verdict on that reading

They are three different facts. The first two already have a pipeline; this
phase adds the third and deliberately stops there. So the load-bearing tests
are the negative ones -- that submitting feedback leaves every rating alone,
leaves the preference evidence byte-identical, and never turns "not really"
into a dislike.

The rest is the boundary every user-scoped route has: the reader comes from
the session, no parameter names one, and one reader cannot see or overwrite
another's answers.

Everything committed here is created under test-only identifiers and removed
afterwards.
"""

import asyncio
import json
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import (
    FEEDBACK_CONFIRMED,
    FEEDBACK_CORRECTED,
    FEEDBACK_TYPES,
    STATUS_COMPLETED,
    SURFACE_TASTE_PROFILE,
    Work,
)
from app.services.concepts.service import (
    SourceLabel,
    apply_source_labels,
    ensure_vocabulary,
)
from app.services.ingestion.anime import AniListAnimeAdapter
from app.services.ingestion.service import ingest_source_work

FIXTURES = Path(__file__).parent / "fixtures"
FEEDBACK_EMAIL_DOMAIN = "@feedback-api.invalid"
# Distinct from every other suite's block, so a full run cannot collide.
FEEDBACK_SOURCE_IDS = (987001, 987002, 987003, 987004)
PASSWORD = "a-sufficiently-long-password"

# The concepts the seeded works carry, by canonical slug. These are the
# targets a reader can answer about; the display names are not.
PSYCHOLOGICAL = "psychological-depth"
MYSTERY = "mystery"

FEEDBACK_URL = "/api/v1/preferences/feedback"


@dataclass
class FeedbackApi:
    client: TestClient
    work_ids: list[str]


@pytest.fixture
def api(database_available: bool) -> Iterator[FeedbackApi]:
    if not database_available:
        pytest.skip("requires a live Postgres instance")

    from app.main import app

    work_ids: list[str] = []

    async def seed() -> None:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(bind=engine, expire_on_commit=False)() as session:
                await ensure_vocabulary(session)
                media = json.loads(
                    (FIXTURES / "anilist_cowboy_bebop.json").read_text(encoding="utf-8")
                )
                for anilist_id in FEEDBACK_SOURCE_IDS:
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
                    work_ids.append(str(work.id))
                await session.commit()
        finally:
            await engine.dispose()

    async def cleanup() -> None:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(bind=engine)() as session:
                refs = ", ".join(f"'{value}'" for value in FEEDBACK_SOURCE_IDS)
                work_ids_sql = (
                    "SELECT id FROM works WHERE source = 'anilist' "
                    f"AND external_ids->>'source_ref' IN ({refs})"
                )
                users_sql = (
                    f"SELECT id FROM users WHERE email LIKE '%{FEEDBACK_EMAIL_DOMAIN}'"
                )
                for statement in (
                    "DELETE FROM user_preference_feedback_events WHERE feedback_id IN "
                    "(SELECT id FROM user_preference_feedback WHERE user_id IN "
                    f"({users_sql}))",
                    f"DELETE FROM user_preference_feedback WHERE user_id IN ({users_sql})",
                    "DELETE FROM user_content_events WHERE interaction_id IN "
                    "(SELECT id FROM user_content_interactions WHERE work_id IN "
                    f"({work_ids_sql}))",
                    f"DELETE FROM user_content_interactions WHERE work_id IN ({work_ids_sql})",
                    f"DELETE FROM work_concepts WHERE work_id IN ({work_ids_sql})",
                    f"DELETE FROM entities WHERE work_id IN ({work_ids_sql})",
                    f"DELETE FROM containers WHERE work_id IN ({work_ids_sql})",
                    f"DELETE FROM work_creators WHERE work_id IN ({work_ids_sql})",
                    "DELETE FROM user_content_events WHERE interaction_id IN ("
                    " SELECT i.id FROM user_content_interactions i JOIN users u"
                    f" ON u.id = i.user_id WHERE u.email LIKE '%{FEEDBACK_EMAIL_DOMAIN}')",
                    "DELETE FROM user_content_interactions WHERE user_id IN "
                    f"({users_sql})",
                    f"DELETE FROM user_sessions WHERE user_id IN ({users_sql})",
                    f"DELETE FROM users WHERE email LIKE '%{FEEDBACK_EMAIL_DOMAIN}'",
                    f"DELETE FROM works WHERE id IN ({work_ids_sql})",
                ):
                    await session.execute(text(statement))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed())
    with TestClient(app) as client:
        try:
            yield FeedbackApi(client=client, work_ids=work_ids)
        finally:
            asyncio.run(cleanup())


def register(api: FeedbackApi, name: str) -> dict:
    response = api.client.post(
        "/api/v1/auth/register",
        json={"email": f"{name}{FEEDBACK_EMAIL_DOMAIN}", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def rate(api: FeedbackApi, headers: dict, work_id: str, rating: int | None) -> None:
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


def a_rated_reader(api: FeedbackApi, name: str) -> dict:
    """A reader with enough history for the psychological concept to appear."""
    headers = register(api, name)
    for work_id, rating in zip(api.work_ids, (9, 10, 9, 8)):
        rate(api, headers, work_id, rating)
    return headers


def submit(
    api: FeedbackApi,
    headers: dict,
    slug: str = PSYCHOLOGICAL,
    feedback: str = FEEDBACK_CONFIRMED,
    **extra,
):
    return api.client.post(
        FEEDBACK_URL,
        json={"concept_slug": slug, "feedback": feedback, **extra},
        headers=headers,
    )


# --- submitting ------------------------------------------------------------


def test_1_an_authenticated_reader_can_confirm_an_inferred_preference(
    api: FeedbackApi,
) -> None:
    headers = a_rated_reader(api, "confirmer")

    response = submit(api, headers, feedback=FEEDBACK_CONFIRMED)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["concept_slug"] == PSYCHOLOGICAL
    assert body["concept_name"] == "Psychological Depth"
    assert body["feedback"] == FEEDBACK_CONFIRMED
    assert body["source"] == SURFACE_TASTE_PROFILE
    assert body["submission_count"] == 1


def test_2_an_authenticated_reader_can_disagree_with_an_inferred_preference(
    api: FeedbackApi,
) -> None:
    headers = a_rated_reader(api, "disagreer")

    response = submit(api, headers, feedback=FEEDBACK_CORRECTED)

    assert response.status_code == 201, response.text
    assert response.json()["feedback"] == FEEDBACK_CORRECTED


def test_3_anonymous_submissions_are_refused(api: FeedbackApi) -> None:
    response = api.client.post(
        FEEDBACK_URL, json={"concept_slug": PSYCHOLOGICAL, "feedback": "confirmed"}
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_4_anonymous_reads_are_refused(api: FeedbackApi) -> None:
    assert api.client.get(FEEDBACK_URL).status_code == 401
    assert api.client.get(f"{FEEDBACK_URL}/{PSYCHOLOGICAL}").status_code == 401


def test_5_feedback_about_a_concept_that_does_not_exist_is_refused(
    api: FeedbackApi,
) -> None:
    """A slug is a reference to canonical vocabulary, never free text."""
    headers = a_rated_reader(api, "inventor")

    response = submit(api, headers, slug="a-concept-nobody-defined")

    assert response.status_code == 404
    assert api.client.get(FEEDBACK_URL, headers=headers).json()["items"] == []


def test_6_a_combination_key_is_not_yet_a_valid_target(api: FeedbackApi) -> None:
    """Documented limitation, asserted rather than left to the docstring.

    A combination's key is two canonical slugs joined, and no concept row has
    that slug, so it is refused exactly like any other unknown target. Phase
    1X supports individual canonical concepts only; see
    `app/models/feedback.py` for why the pair is not yet a stable target.
    """
    headers = a_rated_reader(api, "pairer")

    response = submit(api, headers, slug=f"{MYSTERY}+{PSYCHOLOGICAL}")

    assert response.status_code == 404


def test_7_an_unrecognised_verdict_is_refused(api: FeedbackApi) -> None:
    headers = a_rated_reader(api, "shouter")

    for value in ("dislikes", "maybe", "", "CONFIRMED"):
        response = submit(api, headers, feedback=value)
        assert response.status_code == 422, f"{value!r} should not be accepted"


def test_8_an_unrecognised_surface_is_refused(api: FeedbackApi) -> None:
    headers = a_rated_reader(api, "spoofer")

    response = submit(api, headers, source="somewhere-else")

    assert response.status_code == 422


# --- persistence and history ----------------------------------------------


def test_9_feedback_persists_across_requests(api: FeedbackApi) -> None:
    headers = a_rated_reader(api, "persister")
    submit(api, headers, feedback=FEEDBACK_CONFIRMED)

    items = api.client.get(FEEDBACK_URL, headers=headers).json()["items"]

    assert len(items) == 1
    assert items[0]["concept_slug"] == PSYCHOLOGICAL
    assert items[0]["feedback"] == FEEDBACK_CONFIRMED


def test_10_answering_again_updates_rather_than_duplicating(api: FeedbackApi) -> None:
    headers = a_rated_reader(api, "rethinker")

    submit(api, headers, feedback=FEEDBACK_CONFIRMED)
    second = submit(api, headers, feedback=FEEDBACK_CORRECTED)

    assert second.status_code == 201
    assert second.json()["feedback"] == FEEDBACK_CORRECTED
    assert second.json()["submission_count"] == 2

    items = api.client.get(FEEDBACK_URL, headers=headers).json()["items"]
    assert len(items) == 1, "a second answer must not create a second record"
    assert items[0]["feedback"] == FEEDBACK_CORRECTED


def test_11_the_history_keeps_both_answers(api: FeedbackApi) -> None:
    """Changing one's mind adds a fact; it does not erase one."""
    headers = a_rated_reader(api, "historian")
    submit(api, headers, feedback=FEEDBACK_CONFIRMED)
    submit(api, headers, feedback=FEEDBACK_CORRECTED)

    body = api.client.get(f"{FEEDBACK_URL}/{PSYCHOLOGICAL}", headers=headers).json()

    assert [event["feedback_after"] for event in body["events"]] == [
        FEEDBACK_CONFIRMED,
        FEEDBACK_CORRECTED,
    ]
    assert body["events"][0]["feedback_before"] is None
    assert body["events"][1]["feedback_before"] == FEEDBACK_CONFIRMED
    # The current verdict is stated, never inferred from the last event.
    assert body["current"]["feedback"] == FEEDBACK_CORRECTED


def test_12_the_latest_verdict_is_deterministic_after_repeated_answers(
    api: FeedbackApi,
) -> None:
    headers = a_rated_reader(api, "repeater")
    for value in (FEEDBACK_CONFIRMED, FEEDBACK_CORRECTED, FEEDBACK_CONFIRMED):
        submit(api, headers, feedback=value)

    body = api.client.get(f"{FEEDBACK_URL}/{PSYCHOLOGICAL}", headers=headers).json()

    assert body["current"]["feedback"] == FEEDBACK_CONFIRMED
    assert body["current"]["submission_count"] == 3
    assert len(body["events"]) == 3


def test_12b_answering_again_inside_one_clock_tick_still_works(
    api: FeedbackApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two answers arriving fast must not become a 500.

    `updated_at` carries a server-side `onupdate`. Writing it the value it
    already holds reads as *unchanged* to the ORM, so the server-side default
    takes the write instead -- and a server-written column comes back
    expired. Projecting the row then lazy-loads on an async session, which
    cannot await, and the reader gets an error for pressing a button twice.

    The trigger is two writes sharing a timestamp, so the test stops the wall
    clock: the worst clock there is, and the one that makes the guarantee
    unconditional.
    """
    from app.core import clock

    frozen = datetime.now(timezone.utc)

    class StoppedWallClock:
        @staticmethod
        def now(tz: timezone | None = None) -> datetime:
            return frozen

    monkeypatch.setattr(clock, "datetime", StoppedWallClock)
    monkeypatch.setattr(clock, "_last", None)

    headers = a_rated_reader(api, "fast-fingers")
    for value in (FEEDBACK_CONFIRMED, FEEDBACK_CORRECTED, FEEDBACK_CONFIRMED):
        assert submit(api, headers, feedback=value).status_code == 201

    body = api.client.get(f"{FEEDBACK_URL}/{PSYCHOLOGICAL}", headers=headers).json()

    assert body["current"]["feedback"] == FEEDBACK_CONFIRMED
    assert body["current"]["submission_count"] == 3
    # And the history is still a sequence, not three answers in a bag.
    assert [event["feedback_after"] for event in body["events"]] == [
        FEEDBACK_CONFIRMED,
        FEEDBACK_CORRECTED,
        FEEDBACK_CONFIRMED,
    ]


def test_13_a_concept_never_answered_about_is_not_a_404(api: FeedbackApi) -> None:
    """Nothing said yet is a real answer, and not the same as no such concept."""
    headers = a_rated_reader(api, "silent")

    body = api.client.get(f"{FEEDBACK_URL}/{MYSTERY}", headers=headers).json()

    assert body["current"] is None
    assert body["events"] == []


def test_14_the_listing_is_ordered_by_canonical_slug(api: FeedbackApi) -> None:
    headers = a_rated_reader(api, "sorter")
    submit(api, headers, slug=PSYCHOLOGICAL)
    submit(api, headers, slug=MYSTERY)

    slugs = [
        item["concept_slug"]
        for item in api.client.get(FEEDBACK_URL, headers=headers).json()["items"]
    ]

    assert slugs == sorted(slugs)
    assert slugs == [MYSTERY, PSYCHOLOGICAL]


# --- isolation -------------------------------------------------------------


def test_15_one_reader_never_sees_another_readers_feedback(api: FeedbackApi) -> None:
    first = a_rated_reader(api, "reader-one")
    second = a_rated_reader(api, "reader-two")

    submit(api, first, feedback=FEEDBACK_CONFIRMED)

    assert api.client.get(FEEDBACK_URL, headers=second).json()["items"] == []
    other = api.client.get(f"{FEEDBACK_URL}/{PSYCHOLOGICAL}", headers=second).json()
    assert other["current"] is None


def test_16_one_reader_never_overwrites_another_readers_feedback(
    api: FeedbackApi,
) -> None:
    first = a_rated_reader(api, "owner")
    second = a_rated_reader(api, "intruder")

    submit(api, first, feedback=FEEDBACK_CONFIRMED)
    submit(api, second, feedback=FEEDBACK_CORRECTED)

    assert (
        api.client.get(FEEDBACK_URL, headers=first).json()["items"][0]["feedback"]
        == FEEDBACK_CONFIRMED
    )
    assert (
        api.client.get(FEEDBACK_URL, headers=second).json()["items"][0]["feedback"]
        == FEEDBACK_CORRECTED
    )


def test_17_the_endpoints_accept_no_user_identifier_at_all() -> None:
    """The isolation boundary, asserted on the schema rather than a response."""
    from app.main import app

    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1/preferences/feedback"):
            continue
        assert "user" not in path
        dependant = getattr(route, "dependant", None)
        assert dependant is not None
        for parameter in dependant.query_params:
            assert "user" not in parameter.name


def test_18_a_user_id_in_the_body_is_ignored_not_honoured(api: FeedbackApi) -> None:
    victim = a_rated_reader(api, "victim")
    attacker = a_rated_reader(api, "attacker")
    victim_id = api.client.get("/api/v1/auth/me", headers=victim).json()["id"]

    response = submit(api, attacker, feedback=FEEDBACK_CORRECTED, user_id=victim_id)

    assert response.status_code == 201
    # The extra field changed nothing: the victim still has no feedback.
    assert api.client.get(FEEDBACK_URL, headers=victim).json()["items"] == []


# --- the line this phase exists to hold ------------------------------------


def test_19_feedback_writes_no_rating(api: FeedbackApi) -> None:
    """A disagreement is not a 1/10, and must not be stored as one."""
    headers = a_rated_reader(api, "unrater")
    before = api.client.get("/api/v1/library", headers=headers).json()["items"]
    ratings_before = {
        entry["work"]["id"]: entry["user_state"]["rating"] for entry in before
    }

    submit(api, headers, feedback=FEEDBACK_CORRECTED)

    after = api.client.get("/api/v1/library", headers=headers).json()["items"]
    ratings_after = {
        entry["work"]["id"]: entry["user_state"]["rating"] for entry in after
    }
    assert ratings_after == ratings_before
    assert set(ratings_after.values()) == {9, 10, 8}


def test_20_feedback_adds_no_library_entry_and_no_interaction_event(
    api: FeedbackApi,
) -> None:
    headers = a_rated_reader(api, "quiet")
    work_id = api.work_ids[0]
    before = api.client.get(f"/api/v1/library/{work_id}/events", headers=headers).json()

    submit(api, headers, feedback=FEEDBACK_CORRECTED)

    after = api.client.get(f"/api/v1/library/{work_id}/events", headers=headers).json()
    assert len(after) == len(before)
    assert api.client.get("/api/v1/library", headers=headers).json()["total"] == len(
        api.work_ids
    )


def test_21_feedback_does_not_move_the_preference_evidence(api: FeedbackApi) -> None:
    """Phase 1X stores the verdict and stops.

    The engine's output must be byte-identical either side of a
    disagreement. If a later phase decides feedback should shift inferred
    preference, it will do so deliberately and this test will be the one that
    says so.
    """
    headers = a_rated_reader(api, "unmoved")
    before = api.client.get("/api/v1/preferences", headers=headers).json()

    submit(api, headers, feedback=FEEDBACK_CORRECTED)

    after = api.client.get("/api/v1/preferences", headers=headers).json()
    assert after == before


def test_22_feedback_does_not_move_the_dashboard(api: FeedbackApi) -> None:
    headers = a_rated_reader(api, "unmoved-dashboard")
    before = api.client.get("/api/v1/preferences/dashboard", headers=headers).json()

    submit(api, headers, feedback=FEEDBACK_CORRECTED)
    submit(api, headers, slug=MYSTERY, feedback=FEEDBACK_CORRECTED)

    after = api.client.get("/api/v1/preferences/dashboard", headers=headers).json()
    assert after == before


def test_23_disagreement_is_not_recorded_as_a_dislike(api: FeedbackApi) -> None:
    """`corrected` means "that reading is wrong", not "I dislike this".

    Someone may simply not care about a concept. Inferring the opposite
    preference from a disagreement would invent an opinion they never gave,
    which is the same mistake as the inference they were correcting.
    """
    headers = a_rated_reader(api, "indifferent")
    submit(api, headers, feedback=FEEDBACK_CORRECTED)

    dashboard = api.client.get("/api/v1/preferences/dashboard", headers=headers).json()

    keys = {item["key"] for item in dashboard["dislikes"]}
    assert PSYCHOLOGICAL not in keys
    stored = api.client.get(FEEDBACK_URL, headers=headers).json()["items"][0]
    assert stored["feedback"] == FEEDBACK_CORRECTED
    assert "dislike" not in json.dumps(stored).lower()


def test_24_the_response_never_claims_the_model_has_learned_anything(
    api: FeedbackApi,
) -> None:
    headers = a_rated_reader(api, "honest")

    body = json.dumps(submit(api, headers).json()).lower()

    # The payload reports what was stored and nothing about the engine, so a
    # client cannot honestly render "your profile has been corrected" from it.
    for phrase in ("improved", "retrained", "learned", "updated your", "your taste"):
        assert phrase not in body


# --- the contract itself ---------------------------------------------------


def test_25_the_vocabulary_states_what_corrected_does_and_does_not_mean(
    api: FeedbackApi,
) -> None:
    headers = a_rated_reader(api, "reader-of-docs")

    body = api.client.get(f"{FEEDBACK_URL}/vocabulary", headers=headers).json()

    assert body["values"] == list(FEEDBACK_TYPES)
    assert body["affects_preference_engine"] is False
    assert "not" in body["meanings"]["corrected"].lower()
    assert "dislike" in body["meanings"]["corrected"].lower()


def test_26_the_vocabulary_route_is_not_swallowed_by_the_slug_route(
    api: FeedbackApi,
) -> None:
    """`/feedback/vocabulary` must not resolve as a concept named "vocabulary"."""
    headers = a_rated_reader(api, "router")

    body = api.client.get(f"{FEEDBACK_URL}/vocabulary", headers=headers).json()

    assert "values" in body and "concept_slug" not in body


def test_27_no_scoring_internals_appear_in_a_feedback_payload(
    api: FeedbackApi,
) -> None:
    headers = a_rated_reader(api, "no-internals")
    submit(api, headers)

    payload = json.dumps(
        {
            "one": submit(api, headers).json(),
            "list": api.client.get(FEEDBACK_URL, headers=headers).json(),
            "history": api.client.get(
                f"{FEEDBACK_URL}/{PSYCHOLOGICAL}", headers=headers
            ).json(),
        }
    )

    for forbidden in (
        "preference_evidence",
        "confidence",
        "normalized_rating",
        "baseline",
        "rating",
        "concept_id",
        "user_id",
        "supporting_work_ids",
    ):
        assert forbidden not in payload, f"{forbidden} leaked into a feedback payload"


def test_28_the_stored_target_is_the_canonical_concept_not_a_display_string(
    api: FeedbackApi,
) -> None:
    """Identity is the slug; the name travels only so a client can render it."""
    headers = a_rated_reader(api, "canonical")
    submit(api, headers)

    item = api.client.get(FEEDBACK_URL, headers=headers).json()["items"][0]

    assert item["concept_slug"] == PSYCHOLOGICAL
    assert item["concept_name"] == "Psychological Depth"
    # A display name is not a target.
    assert submit(api, headers, slug="Psychological Depth").status_code == 404


def test_29_a_reader_with_no_ratings_may_still_record_feedback(
    api: FeedbackApi,
) -> None:
    """Feedback is about a concept, not about a dashboard position.

    A reader whose profile shows nothing has still met concepts elsewhere in
    the product, and the record must not depend on where they happened to be
    standing when they answered.
    """
    headers = register(api, "unrated")

    response = submit(api, headers, feedback=FEEDBACK_CONFIRMED)

    assert response.status_code == 201
    assert (
        api.client.get(FEEDBACK_URL, headers=headers).json()["items"][0]["feedback"]
        == FEEDBACK_CONFIRMED
    )


def test_30_feedback_survives_a_new_session(api: FeedbackApi) -> None:
    headers = a_rated_reader(api, "returning")
    submit(api, headers, feedback=FEEDBACK_CORRECTED)
    api.client.post("/api/v1/auth/logout", headers=headers)

    again = api.client.post(
        "/api/v1/auth/login",
        json={"email": f"returning{FEEDBACK_EMAIL_DOMAIN}", "password": PASSWORD},
    )
    fresh = {"Authorization": f"Bearer {again.json()['access_token']}"}

    items = api.client.get(FEEDBACK_URL, headers=fresh).json()["items"]
    assert items[0]["feedback"] == FEEDBACK_CORRECTED


def test_31_a_malformed_body_is_refused_before_anything_is_written(
    api: FeedbackApi,
) -> None:
    headers = a_rated_reader(api, "malformed")

    assert api.client.post(FEEDBACK_URL, json={}, headers=headers).status_code == 422
    assert (
        api.client.post(
            FEEDBACK_URL, json={"concept_slug": PSYCHOLOGICAL}, headers=headers
        ).status_code
        == 422
    )
    assert api.client.get(FEEDBACK_URL, headers=headers).json()["items"] == []


def test_32_the_feedback_service_never_touches_the_rating_tables() -> None:
    """A structural check: the module has no path to a rating at all.

    Cheaper and more durable than any behavioural test -- if a later edit
    reaches for `UserContentInteraction` here, this fails before the
    behaviour does.
    """
    source = (
        Path(__file__).parents[1] / "app/services/preference/feedback.py"
    ).read_text(encoding="utf-8")
    body = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    # The docstring names them to explain the refusal; the code must not.
    code = body.split('"""', 2)[-1]
    for forbidden in ("UserContentInteraction", "rating", "normalized"):
        assert forbidden not in code, f"feedback service reaches for {forbidden}"


def test_33_uuid_targets_are_refused(api: FeedbackApi) -> None:
    """A concept id is not the public identity, and is not accepted as one."""
    headers = a_rated_reader(api, "uuid-user")

    assert submit(api, headers, slug=str(uuid.uuid4())).status_code == 404
