"""Phase 1Y: browsing and searching the canonical corpus.

Discovery is where the product stops being a corpus viewer, so what is under
test is mostly the boundary rather than the retrieval:

    the same works for everyone     signing in attaches `user_state` and
                                    changes nothing else -- not the set, not
                                    the order. Discovery is not a
                                    recommender wearing a filter's clothes.

    lexical, not semantic           someone typing "Monster" wants *Monster*.
                                    That has to work without an embedding
                                    model, and a `%` in the box is a
                                    character, not a pattern.

    honest metadata                 genres exist only where a source stated
                                    them. The facets say so rather than the
                                    UI pretending literature has genres.

    a page, not the corpus          `total` is what matched; `items` is what
                                    was asked for. Ordering is deterministic
                                    so a page boundary cannot drop or repeat.

Four works are seeded under test-only source refs and removed afterwards.
Their titles share a nonsense token so every assertion can scope itself to
them without depending on what the real corpus happens to contain.
"""

import asyncio
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import Work
from app.services.concepts.service import (
    SourceLabel,
    apply_source_labels,
    ensure_vocabulary,
)
from app.services.discovery_service import MAX_PAGE_SIZE, escape_like
from app.services.ingestion.anime import AniListAnimeAdapter
from app.services.ingestion.literature import PlainTextLiteratureAdapter
from app.services.ingestion.service import ingest_source_work

FIXTURES = Path(__file__).parent / "fixtures"
DISCOVERY_EMAIL_DOMAIN = "@discovery-api.invalid"
PASSWORD = "a-sufficiently-long-password"

# A token no real work's title contains, so every query can be scoped to the
# seeded set without asserting anything about the ingested corpus.
TOKEN = "Zephyrine"

ANIME_IDS = (988001, 988002)
LIT_REFS = ("discovery-test-silver", "discovery-test-harbour")

WORKS_URL = "/api/v1/works"


@dataclass
class DiscoveryApi:
    client: TestClient
    # Title -> id, for the four seeded works.
    ids: dict[str, str]


@pytest.fixture
def api(database_available: bool) -> Iterator[DiscoveryApi]:
    if not database_available:
        pytest.skip("requires a live Postgres instance")

    from app.main import app

    ids: dict[str, str] = {}

    async def seed() -> None:
        engine = create_async_engine(get_settings().database_url)
        media = json.loads(
            (FIXTURES / "anilist_cowboy_bebop.json").read_text(encoding="utf-8")
        )
        try:
            async with async_sessionmaker(bind=engine, expire_on_commit=False)() as session:
                await ensure_vocabulary(session)

                # Two anime: an exact title match and a prefix match, with
                # source-stated genres.
                anime = [
                    (ANIME_IDS[0], TOKEN, ["Psychological", "Drama"], "Psychological"),
                    (ANIME_IDS[1], f"{TOKEN} Song", ["Mystery"], "Mystery"),
                ]
                for anilist_id, title, genres, label in anime:
                    payload = dict(media)
                    payload["id"] = anilist_id
                    payload["title"] = {
                        "romaji": title,
                        "english": None,
                        "native": f"{title} 原題",
                    }
                    payload["relations"] = {"edges": []}
                    payload["genres"] = genres
                    payload["tags"] = []
                    result = await ingest_source_work(
                        session, AniListAnimeAdapter(media=payload).load()
                    )
                    work = await session.get(Work, result.work_id)
                    await apply_source_labels(
                        session,
                        work=work,
                        labels=[SourceLabel(label, "anilist_tag", rank=88)],
                    )
                    ids[title] = str(work.id)

                # Two literature works: a contains-match, and one that does
                # not match the token at all. Neither has genres, because
                # Gutenberg states none -- which is the point.
                body = (FIXTURES / "chaptered_work.txt").read_text(encoding="utf-8")
                literature = [
                    (LIT_REFS[0], f"The Silver {TOKEN}", "Psychological"),
                    (LIT_REFS[1], "Quiet Harbour (discovery test)", "Tragedy"),
                ]
                for source_ref, title, label in literature:
                    result = await ingest_source_work(
                        session,
                        PlainTextLiteratureAdapter(
                            text=body,
                            title=title,
                            source_ref=source_ref,
                            author="A Discovery Author",
                            source_url=f"https://example.invalid/{source_ref}",
                        ).load(),
                    )
                    work = await session.get(Work, result.work_id)
                    await apply_source_labels(
                        session,
                        work=work,
                        labels=[SourceLabel(label, "gutenberg_subject")],
                    )
                    ids[title] = str(work.id)

                await session.commit()
        finally:
            await engine.dispose()

    async def cleanup() -> None:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(bind=engine)() as session:
                anime_refs = ", ".join(f"'{value}'" for value in ANIME_IDS)
                lit_refs = ", ".join(f"'{value}'" for value in LIT_REFS)
                work_ids = (
                    "SELECT id FROM works WHERE"
                    f" (source = 'anilist' AND external_ids->>'source_ref' IN ({anime_refs}))"
                    f" OR (external_ids->>'source_ref' IN ({lit_refs}))"
                )
                users = (
                    f"SELECT id FROM users WHERE email LIKE '%{DISCOVERY_EMAIL_DOMAIN}'"
                )
                for statement in (
                    "DELETE FROM user_content_events WHERE interaction_id IN "
                    f"(SELECT id FROM user_content_interactions WHERE work_id IN ({work_ids}))",
                    f"DELETE FROM user_content_interactions WHERE work_id IN ({work_ids})",
                    "DELETE FROM user_preference_feedback_events WHERE feedback_id IN "
                    f"(SELECT id FROM user_preference_feedback WHERE user_id IN ({users}))",
                    f"DELETE FROM user_preference_feedback WHERE user_id IN ({users})",
                    "DELETE FROM user_content_events WHERE interaction_id IN "
                    f"(SELECT id FROM user_content_interactions WHERE user_id IN ({users}))",
                    f"DELETE FROM user_content_interactions WHERE user_id IN ({users})",
                    f"DELETE FROM user_sessions WHERE user_id IN ({users})",
                    f"DELETE FROM users WHERE email LIKE '%{DISCOVERY_EMAIL_DOMAIN}'",
                    f"DELETE FROM work_concepts WHERE work_id IN ({work_ids})",
                    "DELETE FROM content_units WHERE container_id IN "
                    f"(SELECT id FROM containers WHERE work_id IN ({work_ids}))",
                    f"DELETE FROM entities WHERE work_id IN ({work_ids})",
                    f"DELETE FROM containers WHERE work_id IN ({work_ids})",
                    f"DELETE FROM work_creators WHERE work_id IN ({work_ids})",
                    f"DELETE FROM works WHERE id IN ({work_ids})",
                    "DELETE FROM creators WHERE name = 'A Discovery Author' AND NOT EXISTS"
                    " (SELECT 1 FROM work_creators wc WHERE wc.creator_id = creators.id)",
                ):
                    await session.execute(text(statement))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed())
    with TestClient(app) as client:
        try:
            yield DiscoveryApi(client=client, ids=ids)
        finally:
            asyncio.run(cleanup())


def register(api: DiscoveryApi, name: str) -> dict:
    response = api.client.post(
        "/api/v1/auth/register",
        json={"email": f"{name}{DISCOVERY_EMAIL_DOMAIN}", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def page(api: DiscoveryApi, headers: dict | None = None, **params) -> dict:
    response = api.client.get(WORKS_URL, params=params or None, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def titles(body: dict) -> list[str]:
    return [item["work"]["title"] for item in body["items"]]


# --- the listing -----------------------------------------------------------


def test_1_anonymous_callers_can_browse_the_corpus(api: DiscoveryApi) -> None:
    """Browsing shared canonical content needs no account."""
    body = page(api)

    assert body["total"] >= 4
    assert len(body["items"]) > 0
    assert all(item["user_state"] is None for item in body["items"])


def test_2_an_authenticated_caller_sees_the_same_works(api: DiscoveryApi) -> None:
    """Signing in attaches state. It does not change what discovery returns."""
    headers = register(api, "browser")

    anonymous = page(api, q=TOKEN)
    authenticated = page(api, headers, q=TOKEN)

    assert titles(anonymous) == titles(authenticated)
    assert [i["work"] for i in anonymous["items"]] == [
        i["work"] for i in authenticated["items"]
    ]
    assert anonymous["total"] == authenticated["total"]


def test_3_user_state_appears_only_for_the_caller_who_owns_it(
    api: DiscoveryApi,
) -> None:
    owner = register(api, "owner")
    other = register(api, "other")
    work_id = api.ids[TOKEN]
    api.client.post("/api/v1/library", json={"work_id": work_id}, headers=owner)

    def state_for(headers: dict | None) -> dict | None:
        body = page(api, headers, q=TOKEN)
        return next(
            item["user_state"]
            for item in body["items"]
            if item["work"]["id"] == work_id
        )

    assert state_for(owner)["status"] == "planned"
    assert state_for(other) is None
    assert state_for(None) is None


def test_4_the_page_reports_what_matched_not_what_was_returned(
    api: DiscoveryApi,
) -> None:
    body = page(api, q=TOKEN, page_size=1)

    assert body["total"] == 3
    assert len(body["items"]) == 1
    assert body["page"] == 1
    assert body["page_size"] == 1


def test_5_pagination_walks_the_whole_result_without_gaps_or_repeats(
    api: DiscoveryApi,
) -> None:
    seen: list[str] = []
    for number in (1, 2, 3):
        seen.extend(titles(page(api, q=TOKEN, page=number, page_size=1)))

    assert len(seen) == 3
    assert len(set(seen)) == 3
    assert seen == titles(page(api, q=TOKEN))


def test_6_a_page_past_the_end_is_empty_rather_than_an_error(
    api: DiscoveryApi,
) -> None:
    body = page(api, q=TOKEN, page=99)

    assert body["items"] == []
    assert body["total"] == 3


def test_7_ordering_is_deterministic_across_identical_requests(
    api: DiscoveryApi,
) -> None:
    assert titles(page(api, page_size=MAX_PAGE_SIZE)) == titles(
        page(api, page_size=MAX_PAGE_SIZE)
    )


def test_8_the_unfiltered_listing_is_ordered_by_title(api: DiscoveryApi) -> None:
    names = titles(page(api, page_size=MAX_PAGE_SIZE))

    assert names == sorted(names, key=str.casefold) or names == sorted(names)


def test_9_page_size_is_capped(api: DiscoveryApi) -> None:
    """One request must not be able to ask for the whole corpus."""
    assert api.client.get(WORKS_URL, params={"page_size": 5000}).status_code == 422


# --- filters ---------------------------------------------------------------


def test_10_works_can_be_filtered_by_domain(api: DiscoveryApi) -> None:
    anime = page(api, q=TOKEN, domain="anime")
    literature = page(api, q=TOKEN, domain="literature")

    assert set(titles(anime)) == {TOKEN, f"{TOKEN} Song"}
    assert titles(literature) == [f"The Silver {TOKEN}"]
    assert all(i["work"]["domain"]["slug"] == "anime" for i in anime["items"])


def test_11_works_can_be_filtered_by_concept(api: DiscoveryApi) -> None:
    """Noema's own vocabulary, which covers every domain."""
    body = page(api, q=TOKEN, concept="psychological-depth")

    # One anime and one literature work carry it -- the point of a
    # cross-domain vocabulary is that the filter is not domain-shaped.
    assert set(titles(body)) == {TOKEN, f"The Silver {TOKEN}"}


def test_12_works_can_be_filtered_by_source_stated_genre(api: DiscoveryApi) -> None:
    body = page(api, q=TOKEN, genre="Mystery")

    assert titles(body) == [f"{TOKEN} Song"]


def test_13_a_genre_filter_returns_nothing_for_literature(api: DiscoveryApi) -> None:
    """Sparse metadata reported honestly, not papered over.

    Gutenberg states no genres, so no literature work can match one. The
    correct answer is an empty result, not a fabricated label.
    """
    body = page(api, q=TOKEN, domain="literature", genre="Drama")

    assert body["total"] == 0
    assert body["items"] == []


def test_14_filters_combine_with_and(api: DiscoveryApi) -> None:
    both = page(api, q=TOKEN, domain="anime", genre="Mystery")
    conflicting = page(api, q=TOKEN, domain="literature", genre="Mystery")

    assert titles(both) == [f"{TOKEN} Song"]
    assert conflicting["total"] == 0


def test_15_an_unknown_filter_value_returns_nothing_rather_than_everything(
    api: DiscoveryApi,
) -> None:
    for params in (
        {"domain": "no-such-domain"},
        {"concept": "no-such-concept"},
        {"genre": "No Such Genre"},
    ):
        body = page(api, **params)
        assert body["total"] == 0, params
        assert body["items"] == [], params


# --- keyword search --------------------------------------------------------


def test_16_a_title_can_be_found_by_typing_it(api: DiscoveryApi) -> None:
    body = page(api, q=TOKEN)

    assert TOKEN in titles(body)


def test_17_title_search_is_case_insensitive(api: DiscoveryApi) -> None:
    lower = titles(page(api, q=TOKEN.lower()))
    upper = titles(page(api, q=TOKEN.upper()))
    mixed = titles(page(api, q=TOKEN.swapcase()))

    assert lower == upper == mixed
    assert TOKEN in lower


def test_18_title_search_ranks_exact_then_prefix_then_contains(
    api: DiscoveryApi,
) -> None:
    """A lexical rule, not a learned one, and explainable in one sentence."""
    assert titles(page(api, q=TOKEN)) == [
        TOKEN,
        f"{TOKEN} Song",
        f"The Silver {TOKEN}",
    ]


def test_19_title_search_matches_the_original_title_too(api: DiscoveryApi) -> None:
    """Someone may type the title the work is known by in its own language."""
    body = page(api, q="原題")

    assert set(titles(body)) == {TOKEN, f"{TOKEN} Song"}


def test_20_a_search_with_no_matches_is_an_empty_page(api: DiscoveryApi) -> None:
    body = page(api, q="qwertyuiopnothing")

    assert body["total"] == 0
    assert body["items"] == []


def test_21_search_results_are_deterministic(api: DiscoveryApi) -> None:
    assert titles(page(api, q=TOKEN)) == titles(page(api, q=TOKEN))


def test_22_like_metacharacters_are_searched_for_not_honoured(
    api: DiscoveryApi,
) -> None:
    """A `%` in the search box is a character, not a wildcard."""
    assert page(api, q="%")["total"] == 0
    assert page(api, q="_")["total"] == 0
    # And the underscore does not quietly match any single character.
    assert page(api, q=f"{TOKEN[:4]}_{TOKEN[5:]}")["total"] == 0


def test_23_escaping_leaves_ordinary_text_alone() -> None:
    assert escape_like("Monster") == "Monster"
    assert escape_like("100%") == "100\\%"
    assert escape_like("a_b") == "a\\_b"


def test_24_a_blank_query_is_no_filter_at_all(api: DiscoveryApi) -> None:
    everything = page(api)

    assert page(api, q="")["total"] == everything["total"]
    assert page(api, q="   ")["total"] == everything["total"]


# --- facets ----------------------------------------------------------------


def test_25_facets_report_what_the_filters_would_match(api: DiscoveryApi) -> None:
    body = api.client.get(f"{WORKS_URL}/facets")
    assert body.status_code == 200
    facets = body.json()

    domains = {d["value"]: d["count"] for d in facets["domains"]}
    assert {"literature", "anime", "manhwa"} <= set(domains)
    assert all(count > 0 for _, count in domains.items() if count)

    concepts = {c["value"] for c in facets["concepts"]}
    assert "psychological-depth" in concepts
    assert all(c["count"] > 0 for c in facets["concepts"])


def test_26_facet_genres_are_the_sources_own_wording(api: DiscoveryApi) -> None:
    facets = api.client.get(f"{WORKS_URL}/facets").json()

    genres = {g["value"]: g for g in facets["genres"]}
    assert "Mystery" in genres
    # No renaming: editing a source's label would be editing what it said.
    assert genres["Mystery"]["label"] == "Mystery"
    assert genres["Mystery"]["count"] > 0


def test_27_every_facet_value_actually_returns_works(api: DiscoveryApi) -> None:
    """The contract the UI relies on to avoid offering dead filters."""
    facets = api.client.get(f"{WORKS_URL}/facets").json()

    for concept in facets["concepts"][:5]:
        assert page(api, concept=concept["value"])["total"] == concept["count"]
    for genre in facets["genres"][:5]:
        assert page(api, genre=genre["value"])["total"] == genre["count"]


def test_28_facets_need_no_authentication(api: DiscoveryApi) -> None:
    assert api.client.get(f"{WORKS_URL}/facets").status_code == 200


def test_29_the_facets_route_is_not_read_as_a_work_id(api: DiscoveryApi) -> None:
    body = api.client.get(f"{WORKS_URL}/facets").json()

    assert "domains" in body and "work" not in body


# --- the product boundary --------------------------------------------------


def test_30_discovery_exposes_no_corpus_internals(api: DiscoveryApi) -> None:
    payload = json.dumps(page(api, page_size=MAX_PAGE_SIZE))

    for forbidden in (
        "content_unit",
        "text_content",
        "embedding",
        "vector",
        "extra_metadata",
        "external_ids",
        "supporting_labels",
        "adapter",
        "provenance",
        "passage",
    ):
        assert forbidden not in payload, f"{forbidden} leaked into discovery"


def test_31_a_work_without_a_cover_reports_an_absence_not_a_guess(
    api: DiscoveryApi,
) -> None:
    body = page(api, q=TOKEN)

    for item in body["items"]:
        assert item["work"]["cover_image_url"] is None


def test_32_literature_reports_no_genres_rather_than_invented_ones(
    api: DiscoveryApi,
) -> None:
    body = page(api, q=TOKEN, domain="literature")

    item = body["items"][0]["work"]
    assert item["genres"] == []
    # But Noema's own vocabulary does cover it, which is the difference.
    assert item["concepts"]


def test_33_the_listing_accepts_no_user_identifier(api: DiscoveryApi) -> None:
    """The isolation rule, asserted on the route rather than a response."""
    from app.main import app

    for route in app.routes:
        if getattr(route, "path", "") != WORKS_URL:
            continue
        names = {p.name for p in route.dependant.query_params}
        assert not any("user" in name for name in names)


def test_34_a_user_id_query_parameter_is_ignored(api: DiscoveryApi) -> None:
    owner = register(api, "target")
    attacker_view = page(api, None, q=TOKEN, user_id="whatever")

    api.client.post(
        "/api/v1/library", json={"work_id": api.ids[TOKEN]}, headers=owner
    )

    assert all(item["user_state"] is None for item in attacker_view["items"])
    assert all(
        item["user_state"] is None
        for item in page(api, None, q=TOKEN, user_id="whatever")["items"]
    )


# --- discovery is not recommendation ---------------------------------------


def test_35_two_readers_with_different_histories_get_the_same_results(
    api: DiscoveryApi,
) -> None:
    """Discovery answers "what is there", not "what suits you".

    The moment these diverge, the endpoint has become a recommender without
    saying so -- which is precisely the thing this phase refuses to ship.
    """
    reader = register(api, "rater")
    stranger = register(api, "stranger")

    work_id = api.ids[TOKEN]
    api.client.post("/api/v1/library", json={"work_id": work_id}, headers=reader)
    api.client.patch(
        f"/api/v1/library/{work_id}",
        json={"status": "completed"},
        headers=reader,
    )
    api.client.patch(
        f"/api/v1/library/{work_id}",
        json={"rating": 10, "rating_set": True},
        headers=reader,
    )

    assert titles(page(api, reader, q=TOKEN)) == titles(page(api, stranger, q=TOKEN))
    assert titles(page(api, reader)) == titles(page(api, None))


def test_36_keyword_search_needs_no_embedding_model(api: DiscoveryApi) -> None:
    """Title lookup and semantic retrieval are different endpoints.

    Asserted together so the distinction is visible in one place: `/works?q=`
    returns works, `/search/semantic` returns passages with similarities, and
    neither is a substitute for the other.
    """
    lexical = page(api, q=TOKEN)
    assert titles(lexical)[0] == TOKEN
    assert "similarity" not in json.dumps(lexical)

    semantic = api.client.post(
        "/api/v1/search/semantic", json={"query": TOKEN, "top_k": 3}
    )
    assert semantic.status_code == 200
    body = semantic.json()
    assert body["result_kind"] == "semantic_similarity"
    assert "hits" in body and "items" not in body
