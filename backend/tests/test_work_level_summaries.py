"""Work-level summaries: text that describes a work with nowhere else to live.

Corpus expansion turned up works AniList catalogues with no containers at all
-- a webtoon it knows only as a title. Those works could hold no content unit,
so they could hold no embedding, so semantic search could not see them, and
Wikipedia's perfectly good plot summary had nowhere to go.

The two obvious ways out are both fabrication. Inventing a "Volume 1" to hang
the summary on presents made-up structure as source fact. Loosening the
number-plus-title matching attaches text to the wrong episode. So a unit may
instead say it describes the *work*, and this file is about the line that
keeps that from becoming a loophole:

    it is a fallback     refused outright for a work whose containers already
                         hold text, so it can only add coverage where there
                         was none
    it is still summary  `summary` tier with a TextSource, exactly like every
                         other third-party description
    it invents nothing   no container is created, no number is assigned, and
                         an article with no narrative section yields nothing
    it is ordinary       the same embedding pipeline, the same model, the
                         same retrieval query, the same filters

The last of those is the point of most of the tests below: a work-level unit
should be conspicuously unexceptional everywhere downstream.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.endpoints.search import get_search_encoder
from app.core.config import get_settings
from app.main import app
from app.models import (
    TEXT_TIER_SUMMARY,
    Container,
    ContentUnit,
    Embedding,
    TextSource,
    Work,
)
from app.services.embedding.search import semantic_search
from app.services.embedding.service import (
    OWNER_TYPE_CONTENT_UNIT,
    eligible_units_query,
    embed_content_units,
)
from app.services.embedding.work_search import EVIDENCE_CHARS, semantic_work_search
from app.services.ingestion.manga import AniListMangaAdapter
from app.services.ingestion.rights import assess_mediawiki_rights, unknown_rights
from app.services.ingestion.service import ingest_source_work
from app.services.ingestion.summary_service import (
    attach_work_summary,
    corroborate_work_article,
    distinctive_creator_tokens,
)
from app.services.ingestion.wikipedia import parse_work_summary
from tests.fake_encoder import FakeEncoder

FIXTURES = Path(__file__).parent / "fixtures"
# Far outside the real corpus' AniList ids, so a test work can never collide
# with an ingested one.
TEST_ID_OFFSET = 920000

PAGE_TITLE = "Test Tower (webtoon)"
PAGE_URL = "https://en.wikipedia.org/wiki/Test_Tower_(webtoon)"

CC_BY_SA_4 = {
    "url": "https://creativecommons.org/licenses/by-sa/4.0/deed.en",
    "text": "Creative Commons Attribution-Share Alike 4.0",
}


def article() -> str:
    return (FIXTURES / "wikipedia_work_article.wikitext").read_text(encoding="utf-8")


def cc_rights(revision: str = "4471"):
    return assess_mediawiki_rights(
        CC_BY_SA_4, page_title=PAGE_TITLE, page_url=PAGE_URL, revision_ref=revision
    )


async def make_container_less_work(session: AsyncSession) -> Work:
    """A manhwa the canonical source records no volumes for.

    Not contrived: this is the shape of most of the Korean webtoons in the
    corpus. AniList has the series, the creators and the genres, and no
    volume list whatsoever.
    """
    media = json.loads(
        (FIXTURES / "anilist_manga_solo_leveling.json").read_text(encoding="utf-8")
    )
    media["id"] = TEST_ID_OFFSET + media["id"]
    media["title"] = {"english": "Test Tower", "romaji": "Test Tower", "native": "Test Tower"}
    media["volumes"] = None
    media["relations"] = {"edges": []}

    result = await ingest_source_work(session, AniListMangaAdapter(media=media).load())
    return (
        await session.execute(select(Work).where(Work.id == result.work_id))
    ).scalar_one()


async def make_container_level_work(
    session: AsyncSession, *, offset: int = 2
) -> tuple[Work, ContentUnit]:
    """A manhwa with volumes, and a summary attached to one of them.

    The shape everything worked in before this phase, kept alongside the new
    one so "unchanged" can be asserted rather than assumed.
    """
    media = json.loads(
        (FIXTURES / "anilist_manga_solo_leveling.json").read_text(encoding="utf-8")
    )
    media["id"] = TEST_ID_OFFSET + offset + media["id"]
    media["title"] = {"english": "Test Volumes", "romaji": "Test Volumes", "native": "T"}
    media["volumes"] = 2
    media["relations"] = {"edges": []}
    result = await ingest_source_work(session, AniListMangaAdapter(media=media).load())
    work = (await session.execute(select(Work).where(Work.id == result.work_id))).scalar_one()

    container = (
        await session.execute(
            select(Container)
            .where(Container.work_id == work.id)
            .order_by(Container.sequence_number)
            .limit(1)
        )
    ).scalar_one()
    unit = ContentUnit(
        container_id=container.id,
        unit_type="synopsis",
        sequence_number=1,
        text_content="A hunter walks into a dungeon that nobody has come back out of.",
        text_tier=TEXT_TIER_SUMMARY,
    )
    session.add(unit)
    await session.flush()
    return work, unit


async def attach(session: AsyncSession, work: Work, *, rights=None, revision: str = "4471"):
    return await attach_work_summary(
        session,
        work_id=work.id,
        work_title=work.title,
        parsed=parse_work_summary(article()),
        rights=rights if rights is not None else cc_rights(revision),
        page_title=PAGE_TITLE,
        page_url=PAGE_URL,
        revision_ref=revision,
        retrieved_text=article(),
    )


async def work_level_units(session: AsyncSession, work: Work) -> list[ContentUnit]:
    return list(
        (
            await session.execute(
                select(ContentUnit).where(ContentUnit.work_id == work.id)
            )
        )
        .scalars()
        .all()
    )


@pytest.fixture
async def summarised_work(db_session: AsyncSession):
    """A container-less work that has been given its one work-level summary."""
    work = await make_container_less_work(db_session)
    report = await attach(db_session, work)
    assert report.attached == 1
    return work


# --- 1. a work can have one ----------------------------------------------


async def test_a_container_less_work_can_hold_a_summary(db_session: AsyncSession) -> None:
    work = await make_container_less_work(db_session)
    containers = (
        await db_session.execute(
            select(func.count()).select_from(Container).where(Container.work_id == work.id)
        )
    ).scalar_one()
    assert containers == 0, "the fixture's premise: nowhere to attach to"

    report = await attach(db_session, work)

    assert report.stored is True
    assert (report.attached, report.unchanged, report.superseded) == (1, 0, 0)

    units = await work_level_units(db_session, work)
    assert len(units) == 1
    assert units[0].container_id is None
    assert units[0].work_id == work.id
    assert "beneath the tower" in units[0].text_content


async def test_it_is_summary_tier_and_never_the_works_own_words(
    db_session: AsyncSession, summarised_work: Work
) -> None:
    unit = (await work_level_units(db_session, summarised_work))[0]

    assert unit.text_tier == TEXT_TIER_SUMMARY
    assert unit.text_source_id is not None


async def test_no_container_is_invented_to_hold_it(
    db_session: AsyncSession, summarised_work: Work
) -> None:
    """The refusal this whole feature exists to avoid working around."""
    containers = (
        await db_session.execute(
            select(func.count())
            .select_from(Container)
            .where(Container.work_id == summarised_work.id)
        )
    ).scalar_one()

    assert containers == 0


# --- 2. provenance --------------------------------------------------------


async def test_the_summary_carries_the_provenance_it_arrived_with(
    db_session: AsyncSession, summarised_work: Work
) -> None:
    unit = (await work_level_units(db_session, summarised_work))[0]
    source = (
        await db_session.execute(
            select(TextSource).where(TextSource.id == unit.text_source_id)
        )
    ).scalar_one()

    assert source.source_name == "wikipedia"
    assert source.source_ref == PAGE_TITLE
    assert source.source_url == PAGE_URL
    assert source.revision_ref == "4471"
    assert source.licence == "CC-BY-SA-4.0"
    assert source.permits_storage is True
    assert source.requires_attribution is True
    assert source.attribution_text

    # Which part of which page, so the claim stays checkable.
    assert unit.extra_metadata["scope"] == "work"
    assert unit.extra_metadata["source_section"] == "Synopsis"
    assert unit.extra_metadata["source_page_title"] == PAGE_TITLE


async def test_unclear_rights_leave_no_trace_of_the_text(db_session: AsyncSession) -> None:
    work = await make_container_less_work(db_session)

    report = await attach(db_session, work, rights=unknown_rights("no licence declared"))

    assert report.stored is False
    assert await work_level_units(db_session, work) == []
    assert (
        await db_session.execute(
            select(func.count()).select_from(TextSource).where(TextSource.source_ref == PAGE_TITLE)
        )
    ).scalar_one() == 0


async def test_an_article_with_no_narrative_section_stores_nothing(
    db_session: AsyncSession,
) -> None:
    work = await make_container_less_work(db_session)

    report = await attach_work_summary(
        db_session,
        work_id=work.id,
        work_title=work.title,
        parsed=parse_work_summary("== Production ==\nHow it was made.\n"),
        rights=cc_rights(),
        page_title=PAGE_TITLE,
        page_url=PAGE_URL,
        revision_ref="4471",
        retrieved_text="== Production ==\nHow it was made.\n",
    )

    assert report.stored is False
    assert report.not_stored_reason == "no_work_level_summary_on_the_page"
    assert await work_level_units(db_session, work) == []


# --- the fallback stays a fallback ---------------------------------------


async def test_a_work_whose_containers_hold_text_is_refused(db_session: AsyncSession) -> None:
    """The same narrative at two granularities is a duplicate, not coverage."""
    work, _ = await make_container_level_work(db_session, offset=1)

    report = await attach(db_session, work)

    assert report.stored is False
    assert report.not_stored_reason == "work_already_has_container_level_text"
    assert await work_level_units(db_session, work) == []


# --- idempotence ----------------------------------------------------------


async def test_running_it_again_on_an_unchanged_page_changes_nothing(
    db_session: AsyncSession, summarised_work: Work
) -> None:
    report = await attach(db_session, summarised_work)

    assert (report.attached, report.unchanged, report.superseded) == (0, 1, 0)
    assert len(await work_level_units(db_session, summarised_work)) == 1


async def test_a_new_revision_supersedes_rather_than_accumulating(
    db_session: AsyncSession, summarised_work: Work
) -> None:
    report = await attach(db_session, summarised_work, revision="4472")

    assert (report.attached, report.unchanged, report.superseded) == (0, 0, 1)
    units = await work_level_units(db_session, summarised_work)
    assert len(units) == 1

    source = (
        await db_session.execute(
            select(TextSource).where(TextSource.id == units[0].text_source_id)
        )
    ).scalar_one()
    assert source.revision_ref == "4472"
    # The earlier retrieval survives as history rather than being overwritten.
    assert (
        await db_session.execute(
            select(func.count()).select_from(TextSource).where(TextSource.source_ref == PAGE_TITLE)
        )
    ).scalar_one() == 2


# --- 3. it embeds like anything else -------------------------------------


async def test_it_is_eligible_for_the_ordinary_embedding_pipeline(
    db_session: AsyncSession, summarised_work: Work
) -> None:
    unit = (await work_level_units(db_session, summarised_work))[0]

    eligible = (
        (await db_session.execute(eligible_units_query(work_id=summarised_work.id)))
        .scalars()
        .all()
    )

    assert [u.id for u in eligible] == [unit.id]


async def test_it_embeds_on_the_production_contract(
    db_session: AsyncSession, summarised_work: Work
) -> None:
    unit = (await work_level_units(db_session, summarised_work))[0]
    await _embed(db_session, [unit.id])

    embedding = (
        await db_session.execute(
            select(Embedding).where(
                Embedding.owner_type == OWNER_TYPE_CONTENT_UNIT,
                Embedding.owner_id == unit.id,
            )
        )
    ).scalar_one()

    # Nothing about the vector is special. Same owner type, same width, same
    # normalization -- the unit's parent is not the encoder's business.
    assert embedding.dimension == get_settings().embedding_dimensions
    assert embedding.normalized is True


# --- 4 & 5. retrieval and the work-level fold ----------------------------


@pytest.fixture
async def retrievable(db_session: AsyncSession, summarised_work: Work):
    """One work-level unit and one container-level unit, both embedded.

    Both kinds in one index, because the question these tests ask is whether
    the new one behaves like the old one -- which is not answerable if only
    the new one is there to find.
    """
    unit = (await work_level_units(db_session, summarised_work))[0]
    neighbour, neighbour_unit = await make_container_level_work(db_session)
    encoder = await _embed(db_session, [unit.id, neighbour_unit.id])
    return {
        "work": summarised_work,
        "unit": unit,
        "neighbour": neighbour,
        "neighbour_unit": neighbour_unit,
        "encoder": encoder,
    }


async def test_it_is_returned_by_semantic_search(
    db_session: AsyncSession, retrievable
) -> None:
    hits = await semantic_search(
        db_session, retrievable["encoder"], query=retrievable["unit"].text_content, top_k=1
    )

    assert hits[0].content_unit_id == retrievable["unit"].id
    assert hits[0].similarity > 0.99


async def test_the_hit_resolves_to_its_work_and_to_no_container(
    db_session: AsyncSession, retrievable
) -> None:
    """The work half is always populated; the container half is honestly empty."""
    hit = (
        await semantic_search(
            db_session, retrievable["encoder"], query=retrievable["unit"].text_content, top_k=1
        )
    )[0]

    assert hit.work_id == retrievable["work"].id
    assert hit.work_title == retrievable["work"].title
    assert hit.domain_slug == "manhwa"
    assert hit.text_tier == TEXT_TIER_SUMMARY
    assert hit.container_id is None
    assert hit.container_type is None
    assert hit.container_title is None
    assert hit.container_sequence_number is None


async def test_the_domain_filter_still_narrows_it(
    db_session: AsyncSession, retrievable
) -> None:
    """Filters are applied in SQL, before anything is folded. No leakage."""
    query = retrievable["unit"].text_content

    kept = await semantic_search(
        db_session, retrievable["encoder"], query=query, top_k=5, domain_slug="manhwa"
    )
    excluded = await semantic_search(
        db_session, retrievable["encoder"], query=query, top_k=5, domain_slug="literature"
    )

    assert retrievable["unit"].id in [hit.content_unit_id for hit in kept]
    assert retrievable["unit"].id not in [hit.content_unit_id for hit in excluded]
    assert {hit.domain_slug for hit in excluded} <= {"literature"}


async def test_the_tier_filter_still_narrows_it(
    db_session: AsyncSession, retrievable
) -> None:
    primary_only = await semantic_search(
        db_session,
        retrievable["encoder"],
        query=retrievable["unit"].text_content,
        top_k=5,
        text_tier="primary",
    )

    assert retrievable["unit"].id not in [hit.content_unit_id for hit in primary_only]


async def test_it_folds_into_exactly_one_work_result(
    db_session: AsyncSession, retrievable
) -> None:
    matches = await semantic_work_search(
        db_session,
        retrievable["encoder"],
        query=retrievable["unit"].text_content,
        top_k=5,
        domain_slug="manhwa",
    )

    mine = [match for match in matches if match.work_id == retrievable["work"].id]
    assert len(mine) == 1
    assert mine[0].work_title == retrievable["work"].title
    assert mine[0].matching_passages == 1
    assert mine[0].container_id is None
    # Evidence stays evidence: short enough to recognise the work by, far too
    # short to read it from.
    assert 0 < len(mine[0].excerpt) <= EVIDENCE_CHARS


# --- 6. not a reading interface ------------------------------------------


def test_there_is_no_container_of_it_to_ask_for(public_client) -> None:
    """The one endpoint that serves unit text is addressed by container id.

    A work-level unit has no container, so the public content route has no
    handle on it at all. That is the mechanism, not a policy check: there is
    nothing to forbid because there is nothing to name.
    """
    client, published = public_client

    containers = client.get(f"/api/v1/works/{published['work_id']}/containers")

    assert containers.status_code == 200
    assert containers.json() == []


def test_the_work_endpoints_carry_no_summary_text(public_client) -> None:
    client, published = public_client

    for path in (
        f"/api/v1/works/{published['work_id']}",
        f"/api/v1/works/{published['work_id']}/internal",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert published["text"] not in response.text


def test_search_points_at_the_work_and_does_not_reproduce_it(public_client) -> None:
    """Evidence, not content. The same contract every other hit is held to."""
    client, published = public_client

    response = client.post(
        "/api/v1/search/works",
        json={"query": published["text"], "top_k": 5, "domain": "manhwa"},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    mine = [r for r in results if r["work"]["id"] == published["work_id"]]
    assert mine, "the work is findable"
    assert published["text"] not in response.text
    for result in results:
        assert len(result["evidence"]["excerpt"]) <= EVIDENCE_CHARS
    # Null rather than an invented chapter number: it describes the whole work.
    assert mine[0]["evidence"]["container_id"] is None
    assert mine[0]["evidence"]["container_sequence_number"] is None
    assert mine[0]["evidence"]["text_tier"] == TEXT_TIER_SUMMARY


# --- 7 & 8. nothing that already worked stops working --------------------


async def test_container_level_content_is_untouched_by_any_of_this(
    db_session: AsyncSession, retrievable
) -> None:
    """A container hit still carries its container, in the same fields."""
    neighbour_unit = retrievable["neighbour_unit"]

    hit = (
        await semantic_search(
            db_session, retrievable["encoder"], query=neighbour_unit.text_content, top_k=1
        )
    )[0]

    assert hit.content_unit_id == neighbour_unit.id
    assert hit.work_id == retrievable["neighbour"].id
    assert hit.container_id == neighbour_unit.container_id
    assert hit.container_type == "volume"
    assert hit.container_sequence_number == 1


async def test_both_kinds_of_unit_are_searched_by_one_query(
    db_session: AsyncSession, retrievable
) -> None:
    """One index, one query, no special case for either parent.

    The outer join is the whole change; this is the assertion that it did not
    quietly drop the container-level rows on the way.
    """
    matches = await semantic_work_search(
        db_session, retrievable["encoder"], query="a dungeon nobody came back from", top_k=20
    )
    found = {match.work_id for match in matches}

    assert retrievable["work"].id in found
    assert retrievable["neighbour"].id in found


async def test_every_unit_in_the_corpus_has_exactly_one_parent(
    db_session: AsyncSession,
) -> None:
    """The invariant the check constraint exists to hold, asserted over real rows.

    Deliberately not a count of works or units: the corpus grows, and a test
    that pins its size fails for the wrong reason. What must not change is
    that every unit resolves to exactly one work.
    """
    two_parents = (
        await db_session.execute(
            select(func.count())
            .select_from(ContentUnit)
            .where(ContentUnit.container_id.is_not(None), ContentUnit.work_id.is_not(None))
        )
    ).scalar_one()
    orphans = (
        await db_session.execute(
            select(func.count())
            .select_from(ContentUnit)
            .where(ContentUnit.container_id.is_(None), ContentUnit.work_id.is_(None))
        )
    ).scalar_one()

    assert (two_parents, orphans) == (0, 0)


async def test_every_work_level_unit_in_the_corpus_is_sourced_summary_text(
    db_session: AsyncSession,
) -> None:
    rows = (
        await db_session.execute(
            select(ContentUnit, TextSource)
            .outerjoin(TextSource, ContentUnit.text_source_id == TextSource.id)
            .where(ContentUnit.work_id.is_not(None))
        )
    ).all()

    for unit, source in rows:
        assert unit.text_tier == TEXT_TIER_SUMMARY, f"{unit.id} is not summary tier"
        assert source is not None, f"{unit.id} has no provenance"
        assert source.permits_storage is True, f"{unit.id} stored without permission"


# --- helpers --------------------------------------------------------------


async def _embed(session: AsyncSession, unit_ids) -> FakeEncoder:
    """Run the real (sync) embedding service inside this async transaction."""
    encoder = FakeEncoder()
    raw = await session.connection()
    await raw.run_sync(lambda conn: _embed_sync(conn, encoder, unit_ids))
    await session.flush()
    return encoder


def _embed_sync(sync_conn, encoder, unit_ids) -> None:
    from sqlalchemy.orm import Session

    session = Session(bind=sync_conn)
    units = list(
        session.execute(select(ContentUnit).where(ContentUnit.id.in_(unit_ids))).scalars().all()
    )
    embed_content_units(session, encoder, units=units)
    session.flush()


@pytest.fixture
async def committed_work_summary(database_available: bool):
    """A summarised work the app's own connection can see, removed afterwards.

    The endpoint tests run the app in its own session, so their data has to be
    committed rather than held in a rolled-back transaction. Everything
    written here is deleted again on the way out, so the real corpus is left
    exactly as it was found -- including its work count.
    """
    if not database_available:
        pytest.skip("requires a live Postgres instance")

    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    encoder = FakeEncoder()
    published: dict = {}

    try:
        async with factory() as session:
            work = await make_container_less_work(session)
            await attach(session, work)
            unit = (await work_level_units(session, work))[0]
            connection = await session.connection()
            await connection.run_sync(lambda conn: _embed_sync(conn, encoder, [unit.id]))
            published = {
                "work_id": str(work.id),
                "unit_id": unit.id,
                "text": unit.text_content,
            }
            await session.commit()

        yield encoder, published

        async with factory() as session:
            await session.execute(
                text(
                    "DELETE FROM embeddings WHERE owner_type = 'content_unit' "
                    "AND owner_id IN (SELECT id FROM content_units WHERE work_id = :w)"
                ),
                {"w": published["work_id"]},
            )
            for statement in (
                "DELETE FROM content_units WHERE work_id = :w",
                "DELETE FROM containers WHERE work_id = :w",
                "DELETE FROM entities WHERE work_id = :w",
                "DELETE FROM work_creators WHERE work_id = :w",
                "DELETE FROM works WHERE id = :w",
            ):
                await session.execute(text(statement), {"w": published["work_id"]})
            await session.execute(
                text(
                    "DELETE FROM text_sources WHERE source_ref = :ref "
                    "AND NOT EXISTS (SELECT 1 FROM content_units cu "
                    "WHERE cu.text_source_id = text_sources.id)"
                ),
                {"ref": PAGE_TITLE},
            )
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
def public_client(committed_work_summary):
    """TestClient with the encoder overridden so no model is downloaded."""
    encoder, published = committed_work_summary
    app.dependency_overrides[get_search_encoder] = lambda: encoder
    with TestClient(app) as client:
        yield client, published
    app.dependency_overrides.pop(get_search_encoder, None)


# --- is this article about this work? ------------------------------------
#
# The list-article paths corroborate twice, on the number and the title. A
# whole-work summary has only the article, so the creators AniList recorded
# are the second witness.


def test_a_matching_title_and_a_named_creator_identify_the_article() -> None:
    agrees, evidence = corroborate_work_article(
        page_title="Berserk (manga)",
        page_text="Berserk is a Japanese manga series written by Kentaro Miura.",
        work_titles=["Berserk", None, "ベルセルク"],
        creator_names=["Kentarou Miura", "Kouji Mori"],
    )

    assert agrees is True
    # Romanisation differs -- "Kentarou" and "Kentaro" are the same person --
    # so the surname is what carried it.
    assert evidence == "creator_named:miura"


def test_a_shared_title_with_the_wrong_creators_is_refused() -> None:
    """The case this exists for.

    "Bastard" is a Korean webtoon by Carnby Kim. "Bastard!!" is a Japanese
    manga by Kazushi Hagiwara. The two titles normalize to the same string,
    and attaching one's plot to the other is exactly the fabrication the
    whole feature was built to avoid.
    """
    agrees, evidence = corroborate_work_article(
        page_title="Bastard!!",
        page_text="Bastard!! is a Japanese manga series written by Kazushi Hagiwara.",
        work_titles=["Bastard", None, "베스타드"],
        creator_names=["Carnby Kim", "Yeong-Chan Hwang"],
    )

    assert agrees is False
    assert evidence == "creator_conflict"


def test_an_unrelated_title_is_refused_before_the_creators_are_consulted() -> None:
    agrees, evidence = corroborate_work_article(
        page_title="Something Else Entirely",
        page_text="Written by Kentaro Miura.",
        work_titles=["Berserk"],
        creator_names=["Kentarou Miura"],
    )

    assert (agrees, evidence) == (False, "title_conflict")


def test_a_disambiguator_is_not_a_disagreement() -> None:
    agrees, _ = corroborate_work_article(
        page_title="Noblesse (manhwa)",
        page_text="Noblesse is a South Korean webtoon by Son Jae-ho and Lee Kwang-su.",
        work_titles=["Noblesse"],
        creator_names=["Jae-Ho Son"],
    )

    assert agrees is True


def test_credits_with_nothing_distinctive_leave_the_title_standing_alone() -> None:
    """SIU, the author of Tower of God, is three letters.

    A name that short would match half the encyclopaedia, so it is not used
    as evidence -- and the caller is told the title was all there was.
    """
    agrees, evidence = corroborate_work_article(
        page_title="Tower of God",
        page_text="Tower of God is a South Korean webtoon by SIU.",
        work_titles=["Tower of God"],
        creator_names=["SIU"],
    )

    assert (agrees, evidence) == (True, "title_only")


def test_short_name_fragments_are_never_the_evidence() -> None:
    assert distinctive_creator_tokens(["Carnby Kim", "Min-Ho Kim"]) == {"carnby"}


def test_a_native_title_alone_is_no_evidence_at_all() -> None:
    """A CJK title normalizes to nothing, which must not read as agreement.

    `titles_agree` treats an absent title as "no evidence either way" -- the
    right answer for an episode AniList recorded no title for, and quite the
    wrong one here, where it would let any article through.
    """
    agrees, evidence = corroborate_work_article(
        page_title="Something Unrelated",
        page_text="Written by somebody else entirely.",
        work_titles=[None, "うずまき"],
        creator_names=["Junji Itou"],
    )

    assert (agrees, evidence) == (False, "title_incomparable")


def test_a_native_title_does_not_excuse_a_conflicting_one() -> None:
    agrees, evidence = corroborate_work_article(
        page_title="Something Unrelated",
        page_text="Written by Junji Ito.",
        work_titles=["Uzumaki: Spiral into Horror", "うずまき"],
        creator_names=["Junji Itou"],
    )

    assert (agrees, evidence) == (False, "title_conflict")


# --- the fallback rule, from the other side ------------------------------
#
# `attach_work_summary` already refuses a work whose containers hold text.
# Without the mirror image, the *order* of two ingestion runs would decide
# whether a work describes itself twice in one retrieval space.


async def test_container_ingestion_refuses_a_work_that_holds_the_fallback(
    db_session: AsyncSession, summarised_work: Work
) -> None:
    from app.services.ingestion.summary_service import attach_episode_summaries
    from app.services.ingestion.wikipedia import parse_volume_list

    before = (await work_level_units(db_session, summarised_work))[0]

    report = await attach_episode_summaries(
        db_session,
        work_id=summarised_work.id,
        work_title=summarised_work.title,
        parsed=parse_volume_list(
            "{{Graphic novel list\n| VolumeNumber = 1\n| Summary = A volume summary.\n}}"
        ),
        rights=cc_rights(),
        page_title="List of Test Tower chapters",
        page_url="https://en.wikipedia.org/wiki/List_of_Test_Tower_chapters",
        revision_ref="9001",
        retrieved_text="raw wikitext",
        container_type="volume",
    )

    assert report.stored is False
    assert report.not_stored_reason == "work_has_a_work_level_fallback_summary"
    # Named, so a person can look at it. Nothing acts on it.
    assert report.blocking_work_level_unit_id == before.id


async def test_the_refusal_deletes_nothing_and_re_parents_nothing(
    db_session: AsyncSession, summarised_work: Work
) -> None:
    from app.services.ingestion.summary_service import attach_episode_summaries
    from app.services.ingestion.wikipedia import parse_volume_list

    before = (await work_level_units(db_session, summarised_work))[0]
    text_before, source_before = before.text_content, before.text_source_id

    await attach_episode_summaries(
        db_session,
        work_id=summarised_work.id,
        work_title=summarised_work.title,
        parsed=parse_volume_list(
            "{{Graphic novel list\n| VolumeNumber = 1\n| Summary = A volume summary.\n}}"
        ),
        rights=cc_rights(),
        page_title="List of Test Tower chapters",
        page_url=None,
        revision_ref="9001",
        retrieved_text="raw wikitext",
        container_type="volume",
    )

    after = await work_level_units(db_session, summarised_work)
    assert len(after) == 1
    assert (after[0].id, after[0].text_content, after[0].text_source_id) == (
        before.id,
        text_before,
        source_before,
    )
    # No TextSource was created either: the refusal happens before one exists.
    assert (
        await db_session.execute(
            select(func.count())
            .select_from(TextSource)
            .where(TextSource.source_ref == "List of Test Tower chapters")
        )
    ).scalar_one() == 0


async def test_a_work_without_the_fallback_is_unaffected_by_the_guard(
    db_session: AsyncSession,
) -> None:
    """The guard must not become a refusal of ordinary container ingestion."""
    from app.services.ingestion.summary_service import attach_episode_summaries
    from app.services.ingestion.wikipedia import parse_volume_list

    work, _ = await make_container_level_work(db_session, offset=3)

    report = await attach_episode_summaries(
        db_session,
        work_id=work.id,
        work_title=work.title,
        parsed=parse_volume_list(
            "{{Graphic novel list\n| VolumeNumber = 2\n| Summary = "
            "The second volume follows the hunter deeper underground.\n}}"
        ),
        rights=cc_rights(),
        page_title="List of Test Volumes chapters",
        page_url=None,
        revision_ref="9002",
        retrieved_text="raw wikitext",
        container_type="volume",
    )

    assert report.stored is True
    assert report.attached == 1


async def test_re_running_after_a_cleaner_change_refreshes_the_stored_prose(
    db_session: AsyncSession,
) -> None:
    """Same revision, better cleaning: the stored text follows and says so.

    `superseded` means the source changed. This is the other case, and
    counting them together would hide which one happened.
    """
    from app.services.ingestion.summary_service import attach_episode_summaries
    from app.services.ingestion.wikipedia import parse_volume_list

    work, _ = await make_container_level_work(db_session, offset=4)
    page = "List of Refresh Test chapters"

    async def ingest(summary_text: str):
        return await attach_episode_summaries(
            db_session,
            work_id=work.id,
            work_title=work.title,
            parsed=parse_volume_list(
                f"{{{{Graphic novel list\n| VolumeNumber = 2\n| Summary = {summary_text}\n}}}}"
            ),
            rights=cc_rights(),
            page_title=page,
            page_url=None,
            revision_ref="9003",
            # Identical raw page both times: same revision, same TextSource.
            retrieved_text="unchanged raw wikitext",
            container_type="volume",
        )

    first = await ingest("The hunter meets {{Nihongo|Jin-Woo|진우}} underground.")
    assert first.attached == 1

    again = await ingest("The hunter meets {{Nihongo|Jin-Woo|진우}} underground.")
    assert (again.unchanged, again.refreshed, again.superseded) == (1, 0, 0)

    changed = await ingest("The hunter meets Jin-Woo far underground instead.")
    assert (changed.unchanged, changed.refreshed, changed.superseded) == (0, 1, 0)

    unit = (
        await db_session.execute(
            select(ContentUnit)
            .join(Container, ContentUnit.container_id == Container.id)
            .where(Container.work_id == work.id, Container.sequence_number == 2)
        )
    ).scalar_one()
    assert unit.text_content == "The hunter meets Jin-Woo far underground instead."
