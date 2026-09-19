"""Semantic search tests: pgvector ordering, filters, and the API surface.

Uses the deterministic fake encoder. These verify that retrieval *works* and
that filters are honoured -- not that results are semantically good, which is
assessed by hand against the real model.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.search import get_search_encoder
from app.core.config import get_settings
from app.main import app
from app.models import TEXT_TIER_PRIMARY, TEXT_TIER_SUMMARY, Container, ContentUnit
from app.services.embedding.search import EmptyQueryError, semantic_search
from app.services.embedding.service import embed_content_units
from app.services.ingestion.anime import AniListAnimeAdapter
from app.services.ingestion.literature import PlainTextLiteratureAdapter
from app.services.ingestion.service import ingest_source_work
from tests.fake_encoder import FakeEncoder

FIXTURES = Path(__file__).parent / "fixtures"
DIMENSION = get_settings().embedding_dimensions
TEST_ID_OFFSET = 970000


async def _units_of(session: AsyncSession, work_id) -> list[ContentUnit]:
    return list(
        (
            await session.execute(
                select(ContentUnit)
                .join(Container, ContentUnit.container_id == Container.id)
                .where(Container.work_id == work_id)
            )
        )
        .scalars()
        .all()
    )


@pytest.fixture
async def embedded_corpus(db_session: AsyncSession):
    """A tiny two-domain corpus with embeddings, rolled back afterwards."""
    novel = await ingest_source_work(
        db_session,
        PlainTextLiteratureAdapter(
            text=(FIXTURES / "chaptered_work.txt").read_text(encoding="utf-8"),
            title="Search Test Novel",
            source_ref="search-test-lit",
        ).load(),
    )

    media = json.loads((FIXTURES / "anilist_cowboy_bebop.json").read_text(encoding="utf-8"))
    media["id"] = TEST_ID_OFFSET + media["id"]
    media["episodes"] = 2
    media["relations"] = {"edges": []}
    anime = await ingest_source_work(db_session, AniListAnimeAdapter(media=media).load())

    # Give the anime episodes summary-tier text so both tiers are present.
    containers = (
        (
            await db_session.execute(
                select(Container)
                .where(Container.work_id == anime.work_id)
                .order_by(Container.sequence_number)
            )
        )
        .scalars()
        .all()
    )
    for index, container in enumerate(containers, start=1):
        db_session.add(
            ContentUnit(
                container_id=container.id,
                unit_type="synopsis",
                sequence_number=1,
                text_content=f"The bounty hunters chase a fugitive across the colony, episode {index}.",
                text_tier=TEXT_TIER_SUMMARY,
            )
        )
    await db_session.flush()

    units = await _units_of(db_session, novel.work_id) + await _units_of(
        db_session, anime.work_id
    )

    encoder = FakeEncoder()
    # The embedding service is sync; drive it over this async session's
    # connection so everything stays inside the one rolled-back transaction.
    raw = await db_session.connection()
    await raw.run_sync(
        lambda sync_conn: _embed_sync(sync_conn, encoder, [u.id for u in units])
    )
    await db_session.flush()

    yield {
        "encoder": encoder,
        "literature_work_id": novel.work_id,
        "anime_work_id": anime.work_id,
        "containers": containers,
    }


def _embed_sync(sync_conn, encoder, unit_ids):
    from sqlalchemy.orm import Session

    session = Session(bind=sync_conn)
    units = list(
        session.execute(select(ContentUnit).where(ContentUnit.id.in_(unit_ids)))
        .scalars()
        .all()
    )
    embed_content_units(session, encoder, units=units)
    session.flush()


# --- retrieval mechanics -------------------------------------------------


async def test_search_returns_hits_ordered_by_similarity(
    db_session: AsyncSession, embedded_corpus
) -> None:
    hits = await semantic_search(
        db_session, embedded_corpus["encoder"], query="bounty hunters", top_k=5
    )

    assert hits
    similarities = [hit.similarity for hit in hits]
    assert similarities == sorted(similarities, reverse=True)
    assert all(-1.01 <= s <= 1.01 for s in similarities)


async def test_exact_text_is_its_own_nearest_neighbour(
    db_session: AsyncSession, embedded_corpus
) -> None:
    """Sanity check that pgvector is really doing the ordering."""
    unit = (
        await db_session.execute(
            select(ContentUnit)
            .join(Container, ContentUnit.container_id == Container.id)
            .where(Container.work_id == embedded_corpus["anime_work_id"])
            .limit(1)
        )
    ).scalar_one()

    hits = await semantic_search(
        db_session, embedded_corpus["encoder"], query=unit.text_content, top_k=1
    )

    assert hits[0].content_unit_id == unit.id
    assert hits[0].similarity > 0.99


async def test_top_k_limits_results(db_session: AsyncSession, embedded_corpus) -> None:
    hits = await semantic_search(
        db_session, embedded_corpus["encoder"], query="anything", top_k=3
    )

    assert len(hits) <= 3


async def test_hits_carry_enough_context_to_inspect(
    db_session: AsyncSession, embedded_corpus
) -> None:
    hits = await semantic_search(
        db_session, embedded_corpus["encoder"], query="bounty hunters", top_k=1
    )
    hit = hits[0]

    assert hit.work_title
    assert hit.domain_slug in ("literature", "anime")
    assert hit.text_tier in (TEXT_TIER_PRIMARY, TEXT_TIER_SUMMARY)
    assert hit.text_excerpt
    assert hit.container_id is not None


async def test_empty_query_is_rejected(db_session: AsyncSession, embedded_corpus) -> None:
    with pytest.raises(EmptyQueryError):
        await semantic_search(db_session, embedded_corpus["encoder"], query="   ")


async def test_vectors_from_another_model_are_not_searched(
    db_session: AsyncSession, embedded_corpus
) -> None:
    """Distances between different models' vectors are meaningless."""
    other = FakeEncoder(model_name="some/other-model")

    hits = await semantic_search(db_session, other, query="bounty hunters", top_k=5)

    assert hits == []


# --- filters -------------------------------------------------------------


async def test_domain_filter_restricts_results(
    db_session: AsyncSession, embedded_corpus
) -> None:
    hits = await semantic_search(
        db_session, embedded_corpus["encoder"], query="a story", top_k=20, domain_slug="anime"
    )

    assert hits
    assert {hit.domain_slug for hit in hits} == {"anime"}


async def test_text_tier_filter_separates_primary_from_summary(
    db_session: AsyncSession, embedded_corpus
) -> None:
    primary = await semantic_search(
        db_session,
        embedded_corpus["encoder"],
        query="a story",
        top_k=20,
        text_tier=TEXT_TIER_PRIMARY,
    )
    summary = await semantic_search(
        db_session,
        embedded_corpus["encoder"],
        query="a story",
        top_k=20,
        text_tier=TEXT_TIER_SUMMARY,
    )

    assert {hit.text_tier for hit in primary} == {TEXT_TIER_PRIMARY}
    assert {hit.text_tier for hit in summary} == {TEXT_TIER_SUMMARY}
    assert {h.content_unit_id for h in primary}.isdisjoint({h.content_unit_id for h in summary})


async def test_unfiltered_search_can_span_both_domains(
    db_session: AsyncSession, embedded_corpus
) -> None:
    hits = await semantic_search(
        db_session, embedded_corpus["encoder"], query="a story", top_k=100
    )

    assert {"literature", "anime"} <= {hit.domain_slug for hit in hits}


async def test_work_filter_restricts_results(
    db_session: AsyncSession, embedded_corpus
) -> None:
    hits = await semantic_search(
        db_session,
        embedded_corpus["encoder"],
        query="a story",
        top_k=50,
        work_id=embedded_corpus["anime_work_id"],
    )

    assert hits
    assert {hit.work_id for hit in hits} == {embedded_corpus["anime_work_id"]}


async def test_container_filter_restricts_results(
    db_session: AsyncSession, embedded_corpus
) -> None:
    container = embedded_corpus["containers"][0]

    hits = await semantic_search(
        db_session, embedded_corpus["encoder"], query="a story", top_k=50, container_id=container.id
    )

    assert hits
    assert {hit.container_id for hit in hits} == {container.id}


async def test_contextual_representation_returns_passage_hits(
    db_session: AsyncSession, embedded_corpus
) -> None:
    """Contextual hits identify their source units and never pose as one."""
    from sqlalchemy.orm import Session as SyncSession

    from app.services.embedding.contextual import build_passages_for_corpus, embed_passages
    from app.services.embedding.grouping import GroupingConfig

    config = GroupingConfig()
    encoder = embedded_corpus["encoder"]
    # Scoped to this test's own literature work: building passages for the
    # whole corpus would process thousands of unrelated production units.
    literature_work_id = embedded_corpus["literature_work_id"]

    def build(sync_conn):
        session = SyncSession(bind=sync_conn)
        build_passages_for_corpus(
            session, lambda t: len(t.split()), work_id=literature_work_id, config=config
        )
        embed_passages(
            session, encoder, grouping_config=config.key, work_id=literature_work_id
        )
        session.flush()

    await (await db_session.connection()).run_sync(build)
    await db_session.flush()

    hits = await semantic_search(
        db_session,
        encoder,
        query="the harbour at dawn",
        top_k=5,
        representation="contextual_passage",
        grouping_config=config.key,
    )

    assert hits
    for hit in hits:
        assert hit.representation == "contextual_passage"
        assert hit.passage_id is not None
        assert hit.source_unit_ids, "a contextual hit must name its source units"
        assert hit.unit_count == len(hit.source_unit_ids)
        assert hit.grouping_config == config.key
        # It is a derived representation, not a source unit.
        assert hit.content_unit_id is None


async def test_representations_are_never_blended(
    db_session: AsyncSession, embedded_corpus
) -> None:
    baseline = await semantic_search(
        db_session, embedded_corpus["encoder"], query="a story", top_k=20
    )

    assert baseline
    assert {hit.representation for hit in baseline} == {"content_unit"}


async def test_unknown_representation_is_rejected(
    db_session: AsyncSession, embedded_corpus
) -> None:
    with pytest.raises(ValueError, match="unknown representation"):
        await semantic_search(
            db_session, embedded_corpus["encoder"], query="x", representation="nonsense"
        )


async def test_passage_query_filters_in_sql(
    db_session: AsyncSession, embedded_corpus
) -> None:
    from app.services.embedding.search import build_passage_search_query

    statement = build_passage_search_query(
        [0.0] * DIMENSION,
        model_name="x",
        top_k=5,
        domain_slug="literature",
        text_tier="primary",
        grouping_config="window=3;overlap=1;max_tokens=240",
    )
    compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))

    assert "domains.slug" in compiled
    assert "text_tier" in compiled
    assert "grouping_config" in compiled
    assert "LIMIT 5" in compiled


async def test_filtering_happens_in_sql_not_python(
    db_session: AsyncSession, embedded_corpus
) -> None:
    """top_k must apply after filtering, which only works if SQL filters."""
    from app.services.embedding.search import build_search_query

    statement = build_search_query(
        [0.0] * DIMENSION, model_name="x", top_k=5, domain_slug="anime", text_tier=TEXT_TIER_SUMMARY
    )
    compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))

    assert "domains.slug" in compiled
    assert "text_tier" in compiled
    assert "LIMIT 5" in compiled
    assert "ORDER BY" in compiled


# --- API -----------------------------------------------------------------


@pytest.fixture
async def committed_corpus(database_available: bool):
    """Like `embedded_corpus`, but committed so the API can actually see it.

    The API reads through its own sessions, so a rolled-back fixture would
    leave every endpoint test asserting against zero rows and passing for the
    wrong reason. This commits, then deletes what it created.
    """
    if not database_available:
        pytest.skip("requires a live Postgres instance")

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    encoder = FakeEncoder()
    work_ids = []

    try:
        async with factory() as session:
            novel = await ingest_source_work(
                session,
                PlainTextLiteratureAdapter(
                    text=(FIXTURES / "chaptered_work.txt").read_text(encoding="utf-8"),
                    title="API Search Novel",
                    source_ref="api-search-lit",
                ).load(),
            )
            media = json.loads(
                (FIXTURES / "anilist_cowboy_bebop.json").read_text(encoding="utf-8")
            )
            media["id"] = TEST_ID_OFFSET + 1 + media["id"]
            media["episodes"] = 2
            media["relations"] = {"edges": []}
            anime = await ingest_source_work(session, AniListAnimeAdapter(media=media).load())
            work_ids = [novel.work_id, anime.work_id]

            containers = (
                (
                    await session.execute(
                        select(Container).where(Container.work_id == anime.work_id)
                    )
                )
                .scalars()
                .all()
            )
            for index, container in enumerate(containers, start=1):
                session.add(
                    ContentUnit(
                        container_id=container.id,
                        unit_type="synopsis",
                        sequence_number=1,
                        text_content=(
                            f"The bounty hunters chase a fugitive across the colony, "
                            f"episode {index}."
                        ),
                        text_tier=TEXT_TIER_SUMMARY,
                    )
                )
            await session.flush()

            unit_ids = [
                u.id
                for w in work_ids
                for u in await _units_of(session, w)
            ]
            connection = await session.connection()
            await connection.run_sync(lambda c: _embed_sync(c, encoder, unit_ids))
            await session.commit()

        yield {"encoder": encoder, "work_ids": work_ids}

        async with factory() as session:
            for work_id in work_ids:
                await session.execute(
                    text(
                        "DELETE FROM embeddings WHERE owner_id IN (SELECT cu.id FROM "
                        "content_units cu JOIN containers ct ON ct.id = cu.container_id "
                        "WHERE ct.work_id = :w)"
                    ),
                    {"w": work_id},
                )
                await session.execute(
                    text(
                        "DELETE FROM content_units WHERE container_id IN "
                        "(SELECT id FROM containers WHERE work_id = :w)"
                    ),
                    {"w": work_id},
                )
                for statement in (
                    "DELETE FROM containers WHERE work_id = :w",
                    "DELETE FROM entities WHERE work_id = :w",
                    "DELETE FROM work_creators WHERE work_id = :w",
                    "DELETE FROM works WHERE id = :w",
                ):
                    await session.execute(text(statement), {"w": work_id})
            await session.execute(
                text(
                    "DELETE FROM creators WHERE NOT EXISTS "
                    "(SELECT 1 FROM work_creators wc WHERE wc.creator_id = creators.id)"
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


@pytest.fixture
def search_client(committed_corpus):
    """TestClient with the encoder overridden so no model is downloaded."""
    app.dependency_overrides[get_search_encoder] = lambda: committed_corpus["encoder"]
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(get_search_encoder, None)


def test_api_rejects_empty_query(search_client: TestClient) -> None:
    assert search_client.post("/api/v1/search/semantic", json={"query": ""}).status_code == 422
    assert search_client.post("/api/v1/search/semantic", json={"query": "   "}).status_code == 422


def test_api_rejects_out_of_range_top_k(search_client: TestClient) -> None:
    for top_k in (0, -1, 1000):
        response = search_client.post(
            "/api/v1/search/semantic", json={"query": "x", "top_k": top_k}
        )
        assert response.status_code == 422


def test_api_rejects_unknown_domain(search_client: TestClient) -> None:
    response = search_client.post(
        "/api/v1/search/semantic", json={"query": "x", "domain": "not-a-domain"}
    )

    assert response.status_code == 422
    assert "unknown domain" in response.json()["detail"]


def test_api_rejects_unknown_text_tier(search_client: TestClient) -> None:
    response = search_client.post(
        "/api/v1/search/semantic", json={"query": "x", "text_tier": "interpretation"}
    )

    assert response.status_code == 422
    assert "unknown text_tier" in response.json()["detail"]


def test_api_labels_results_as_semantic_similarity(search_client: TestClient) -> None:
    response = search_client.post("/api/v1/search/semantic", json={"query": "bounty hunters"})

    assert response.status_code == 200
    body = response.json()
    assert body["result_kind"] == "semantic_similarity"
    assert body["metric"] == "cosine"
    assert body["model_name"]


def test_api_returns_real_hits(search_client: TestClient) -> None:
    response = search_client.post(
        "/api/v1/search/semantic",
        json={"query": "The bounty hunters chase a fugitive across the colony, episode 1.",
              "top_k": 5},
    )

    assert response.status_code == 200
    hits = response.json()["hits"]
    assert hits, "the committed corpus should be searchable through the API"
    assert hits[0]["similarity"] > 0.99
    assert hits[0]["text_tier"] == "summary"


def test_api_domain_and_tier_filters_apply(search_client: TestClient) -> None:
    response = search_client.post(
        "/api/v1/search/semantic",
        json={"query": "a story", "domain": "anime", "text_tier": "summary", "top_k": 3},
    )

    assert response.status_code == 200
    hits = response.json()["hits"]
    assert hits
    assert len(hits) <= 3
    for hit in hits:
        assert hit["domain_slug"] == "anime"
        assert hit["text_tier"] == "summary"


def test_api_primary_filter_returns_literature_prose(search_client: TestClient) -> None:
    hits = search_client.post(
        "/api/v1/search/semantic",
        json={"query": "the harbour at dawn", "text_tier": "primary", "top_k": 5},
    ).json()["hits"]

    assert hits
    assert {hit["text_tier"] for hit in hits} == {"primary"}
    assert {hit["domain_slug"] for hit in hits} == {"literature"}


def test_api_rejects_unknown_representation(search_client: TestClient) -> None:
    response = search_client.post(
        "/api/v1/search/semantic", json={"query": "x", "representation": "vibes"}
    )

    assert response.status_code == 422
    assert "unknown representation" in response.json()["detail"]


def test_api_defaults_to_the_content_unit_representation(search_client: TestClient) -> None:
    body = search_client.post("/api/v1/search/semantic", json={"query": "a story"}).json()

    assert body["representation"] == "content_unit"
    assert all(hit["representation"] == "content_unit" for hit in body["hits"])
    assert all(hit["content_unit_id"] for hit in body["hits"])


def test_api_search_creates_no_relationships(search_client: TestClient) -> None:
    """The architectural invariant: similarity never becomes a relationship."""
    search_client.post("/api/v1/search/semantic", json={"query": "isolation", "top_k": 10})

    page = search_client.get("/api/v1/works", params={"page_size": 100}).json()
    for presentation in page["items"]:
        work_id = presentation["work"]["id"]
        relationships = search_client.get(f"/api/v1/works/{work_id}/relationships").json()
        assert all(r["source"] != "computed" for r in relationships)
