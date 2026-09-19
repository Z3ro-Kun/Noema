"""The user-facing work representation, inside rolled-back transactions.

Fixture-scoped per the Phase 1J principle. Two things are under test: that
the product surface shows what a reader needs, and that it shows *nothing*
from the internal corpus.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Concept, Work
from app.schemas.product import ProductCreator
from app.services import auth_service, library_service, product_service
from app.services.concepts.service import (
    SourceLabel,
    apply_source_labels,
    ensure_vocabulary,
)
from app.services.ingestion.anime import AniListAnimeAdapter
from app.services.ingestion.literature import PlainTextLiteratureAdapter
from app.services.ingestion.service import ingest_source_work
from app.services.product_service import select_product_creators

FIXTURES = Path(__file__).parent / "fixtures"
TEST_ID_OFFSET = 970000
PASSWORD = "a-sufficiently-long-password"


async def make_anime(session: AsyncSession) -> Work:
    media = json.loads((FIXTURES / "anilist_cowboy_bebop.json").read_text(encoding="utf-8"))
    media["id"] = TEST_ID_OFFSET + media["id"]
    media["relations"] = {"edges": []}
    result = await ingest_source_work(session, AniListAnimeAdapter(media=media).load())
    return await session.get(Work, result.work_id)


async def make_novel(session: AsyncSession, source_ref: str = "product-test") -> Work:
    result = await ingest_source_work(
        session,
        PlainTextLiteratureAdapter(
            text=(FIXTURES / "chaptered_work.txt").read_text(encoding="utf-8"),
            title="The Lantern Keeper",
            source_ref=source_ref,
            author="A Test Author",
        ).load(),
    )
    return await session.get(Work, result.work_id)


# --- which credits reach the reader --------------------------------------


def test_localisation_credits_are_not_shown() -> None:
    """A dub director directed a dub, not the work."""
    chosen = select_product_creators(
        [
            ("Director", "Shinichirou Watanabe"),
            ("Director (English; Netflix)", "Some Dub Director"),
            ("Translator (French)", "A Translator"),
            ("Lettering (Italian)", "A Letterer"),
        ]
    )

    assert chosen == [ProductCreator(name="Shinichirou Watanabe", role="Director")]


def test_non_localisation_qualifiers_are_kept() -> None:
    """"Story (chs 1-92)" is a real creative credit, not a translation."""
    chosen = select_product_creators([("Story (chs 1-92)", "So-Ryeong Gi")])

    assert chosen == [ProductCreator(name="So-Ryeong Gi", role="Story")]


@pytest.mark.parametrize(
    "role",
    [
        "Key Animation",
        "Storyboard",
        "ADR Director",
        "Producer",
        "Theme Song Performance",
        "production_company",
        "Episode Director",
        "Assistant",
        "Editing",
    ],
)
def test_crew_credits_are_not_product_facing(role: str) -> None:
    """Real credits that belong on a credits page, not a library card."""
    assert select_product_creators([(role, "Somebody")]) == []


def test_credits_are_ordered_by_importance_not_alphabetically() -> None:
    chosen = select_product_creators(
        [
            ("studio", "Sunrise"),
            ("Series Composition", "Keiko Nobumoto"),
            ("Original Creator", "Hajime Yatate"),
            ("Director", "Shinichirou Watanabe"),
        ]
    )

    assert [creator.role for creator in chosen] == [
        "Original Creator",
        "Director",
        "Series Composition",
        "Studio",
    ]


def test_a_person_credited_twice_under_one_role_appears_once() -> None:
    chosen = select_product_creators([("Director", "Hideaki Anno"), ("Director", "Hideaki Anno")])

    assert len(chosen) == 1


def test_a_work_with_no_product_facing_credit_gets_an_empty_list() -> None:
    """A real case, left empty rather than filled with whatever was on hand."""
    assert select_product_creators([("Key Animation", "Somebody")]) == []


# --- the canonical half ---------------------------------------------------


async def test_anime_presentation_carries_what_a_reader_needs(
    db_session: AsyncSession,
) -> None:
    await ensure_vocabulary(db_session)
    work = await make_anime(db_session)
    await apply_source_labels(
        db_session, work=work, labels=[SourceLabel("Psychological", "anilist_tag", rank=80)]
    )

    presentation = await product_service.get_work_presentation(db_session, work.id)
    product = presentation.work

    assert product.title == "Cowboy Bebop"
    assert product.original_title == "カウボーイビバップ"
    assert product.domain.slug == "anime" and product.domain.name == "Anime"
    assert product.media_format == "TV"
    assert product.year == 1998
    assert product.source == "anilist"
    assert "Action" in product.genres
    assert product.synopsis and "bounty" in product.synopsis.lower()
    # This fixture's only product-facing credit is the main studio: its
    # other staff are an ADR director, a composer and theme-song credits,
    # all correctly excluded.
    assert product.creators == [ProductCreator(name="Sunrise", role="Studio")]
    assert [concept.slug for concept in product.concepts] == ["psychological-depth"]


async def test_concepts_carry_no_ingestion_provenance(db_session: AsyncSession) -> None:
    """Supporting labels and community ranks stay internal."""
    await ensure_vocabulary(db_session)
    work = await make_anime(db_session)
    await apply_source_labels(
        db_session, work=work, labels=[SourceLabel("Crime", "anilist_tag", rank=90)]
    )

    presentation = await product_service.get_work_presentation(db_session, work.id)
    concept = presentation.work.concepts[0]

    assert set(concept.model_dump()) == {"slug", "name", "concept_type"}


async def test_literature_absences_are_represented_cleanly(
    db_session: AsyncSession,
) -> None:
    """Gutenberg supplies no synopsis, no genres and no cover. Say so."""
    work = await make_novel(db_session)

    product = (await product_service.get_work_presentation(db_session, work.id)).work

    assert product.synopsis is None
    assert product.cover_image_url is None
    assert product.genres == []
    assert product.media_format is None
    assert product.year is None
    # What literature *does* have still shows.
    assert product.creators == [ProductCreator(name="A Test Author", role="Author")]
    assert product.domain.slug == "literature"


async def test_a_synopsis_is_never_taken_from_the_works_own_text(
    db_session: AsyncSession,
) -> None:
    """The opening of a novel is corpus content, not a synopsis."""
    work = await make_novel(db_session)
    # A line of actual narrative prose, not the title or byline.
    body = (FIXTURES / "chaptered_work.txt").read_text(encoding="utf-8")
    prose = max((line.strip() for line in body.splitlines()), key=len)[:60]

    product = (await product_service.get_work_presentation(db_session, work.id)).work

    assert product.synopsis is None
    assert len(prose) > 30
    assert prose not in json.dumps(product.model_dump(mode="json"))


async def test_no_cover_url_is_invented(db_session: AsyncSession) -> None:
    """No ingested source recorded cover art, so every work returns null."""
    anime = await make_anime(db_session)
    novel = await make_novel(db_session, "product-cover-test")

    for work in (anime, novel):
        product = (await product_service.get_work_presentation(db_session, work.id)).work
        assert product.cover_image_url is None


# --- what must never appear ----------------------------------------------


async def test_the_presentation_exposes_no_internal_corpus_structures(
    db_session: AsyncSession,
) -> None:
    await ensure_vocabulary(db_session)
    anime = await make_anime(db_session)
    novel = await make_novel(db_session, "product-privacy-test")
    await apply_source_labels(
        db_session, work=anime, labels=[SourceLabel("Crime", "anilist_tag", rank=90)]
    )

    for work in (anime, novel):
        presentation = await product_service.get_work_presentation(db_session, work.id)
        serialised = json.dumps(presentation.model_dump(mode="json"))
        for forbidden in (
            "text_content",
            "content_unit",
            "container",
            "embedding",
            "contextual_passage",
            "experiment",
            "supporting_labels",
            "extra_metadata",
            "external_ids",
            "provenance",
            "adapter",
            "text_source",
            "source_hash",
        ):
            assert forbidden not in serialised, f"{forbidden} leaked for {work.title}"


async def test_the_product_work_model_has_no_user_fields() -> None:
    """Structural: there is nowhere on the canonical half to put user state."""
    from app.schemas.product import ProductWork

    fields = set(ProductWork.model_fields)
    assert not {"status", "rating", "user_id", "added_at", "user_state"} & fields


# --- canonical vs user separation ----------------------------------------


async def test_the_canonical_half_is_identical_for_two_users(
    db_session: AsyncSession,
) -> None:
    """A user's interaction must never alter the canonical Work response."""
    await ensure_vocabulary(db_session)
    work = await make_anime(db_session)
    await apply_source_labels(
        db_session, work=work, labels=[SourceLabel("Crime", "anilist_tag", rank=90)]
    )
    anonymous = await product_service.get_work_presentation(db_session, work.id)

    alice = await auth_service.register_user(
        db_session, email="alice@product.test", password=PASSWORD
    )
    bob = await auth_service.register_user(
        db_session, email="bob@product.test", password=PASSWORD
    )
    for user, status, rating in ((alice, "completed", 10), (bob, "abandoned", 2)):
        await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)
        await library_service.set_status(
            db_session, user_id=user.id, work_id=work.id, status=status
        )
        await library_service.set_rating(
            db_session, user_id=user.id, work_id=work.id, rating=rating
        )

    for_alice = await product_service.get_work_presentation(
        db_session, work.id, user_id=alice.id
    )
    for_bob = await product_service.get_work_presentation(db_session, work.id, user_id=bob.id)

    assert for_alice.work == for_bob.work == anonymous.work
    assert for_alice.user_state.rating == 10
    assert for_bob.user_state.rating == 2
    assert anonymous.user_state is None


async def test_a_user_with_no_interaction_gets_a_null_user_state(
    db_session: AsyncSession,
) -> None:
    work = await make_anime(db_session)
    user = await auth_service.register_user(
        db_session, email="stranger@product.test", password=PASSWORD
    )

    presentation = await product_service.get_work_presentation(
        db_session, work.id, user_id=user.id
    )

    assert presentation.user_state is None
    assert presentation.work.title == "Cowboy Bebop"


async def test_adding_to_a_library_duplicates_no_canonical_row(
    db_session: AsyncSession,
) -> None:
    await ensure_vocabulary(db_session)
    work = await make_anime(db_session)
    await apply_source_labels(
        db_session, work=work, labels=[SourceLabel("Crime", "anilist_tag", rank=90)]
    )

    async def counts():
        works = await db_session.execute(
            select(func.count()).select_from(Work).where(Work.id == work.id)
        )
        concepts = await db_session.execute(select(func.count()).select_from(Concept))
        return works.scalar_one(), concepts.scalar_one()

    before = await counts()
    for name in ("one", "two", "three"):
        user = await auth_service.register_user(
            db_session, email=f"{name}@product.test", password=PASSWORD
        )
        await library_service.add_to_library(db_session, user_id=user.id, work_id=work.id)

    assert await counts() == before


async def test_listing_presentations_is_not_an_n_plus_one(
    db_session: AsyncSession,
) -> None:
    """One extra work must not mean one extra round of queries.

    Guards the batched loaders: the naive shape here is a creators query and
    a concepts query per work, which is invisible until a library gets long.
    """
    await ensure_vocabulary(db_session)
    await make_anime(db_session)
    for index in range(4):
        await make_novel(db_session, f"product-n-plus-one-{index}")
    await db_session.flush()

    statements: list[str] = []
    connection = await db_session.connection()

    from sqlalchemy import event

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(connection.sync_engine, "before_cursor_execute", record)
    try:
        few = await product_service.list_work_presentations(db_session, limit=2)
        count_for_few = len(statements)
        statements.clear()
        many = await product_service.list_work_presentations(db_session, limit=50)
        count_for_many = len(statements)
    finally:
        event.remove(connection.sync_engine, "before_cursor_execute", record)

    assert len(many) > len(few)
    # Constant, not proportional to the number of works.
    assert count_for_many == count_for_few
